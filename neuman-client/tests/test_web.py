"""The chat UI's own source, read as text.

The chat window is four files with no build step, so a test is what `grep` would
be if it were repeatable: the rules it asserts are the ones that are easy to lose in
an edit and invisible when lost — a control that stops dismissing, a transcript
that clips, a palette whose contrast drops below legible, an element that
follows the tail before it grows.

These lived in the server's suite while the server served the UI. They are the
client's: nothing here starts a server, and the only file it reads is its own.
"""

from __future__ import annotations

import re
from pathlib import Path

# Resolved from this file, so the tests depend on their own tree rather than on
# the working directory somebody happened to run pytest from.
WEB = Path(__file__).resolve().parent.parent / "web" / "src"


def _handle(code: str) -> str:
    """The transcript renderer's body, without the rail's phase tracker.

    There are two switches over `frame.type`: `trackViewFrame` folds the frame
    into the session's rail state, and `handle` draws it into a transcript. A
    source test about what is *drawn* has to name the second — with `code` it
    used to read one switch, and the first `case "done"` it finds is now the
    rail's, which would pass or fail for the wrong reason (D63).
    """

    match = re.search(r"function handle\(frame\)\s*\{(.*?)\n\}", code, re.S)
    assert match, "handle() is gone; this test would pass vacuously"
    return match.group(1)


def test_the_client_does_not_mark_an_approval_it_could_not_send():
    """A verdict the socket dropped must leave the buttons alone.

    `send()` returns false when the socket is closed. `decide()` ignored it and
    removed the Approve/Deny buttons anyway, so the card read "approved" while the
    server never received the verdict — the turn then waited out the full
    `approval_timeout_s` (300s) and the session looked frozen with no way to
    retry.

    Asserted against the source because there is no DOM harness here, in the same
    spirit as `test_every_route_the_browser_client_calls_exists`: crude, and the
    only thing that cannot go stale.
    """

    source = (WEB / "neuman.js").read_text()

    match = re.search(r"function decide\([^)]*\)\s*\{(.*?)\n\}", source, re.S)
    assert match, "decide() not found; this test would pass vacuously"
    body = match.group(1)

    assert "send(" in body, body
    # The send must gate what follows, rather than being a statement on its own.
    assert re.search(r"if\s*\(\s*!send\(", body), (
        "decide() calls send() without checking it; a dropped socket silently "
        "swallows the verdict"
    )


def test_the_client_reconnects_a_dropped_socket():
    """A session waiting on approval is exactly when a socket idles out.

    Without reconnection the only recovery was a page reload, which is not
    discoverable — the UI just stopped responding. Reconnection is per view now:
    a background session's socket has no reader to retry it, and its turn is
    cancelled server-side the moment the connection goes.
    """

    source = (WEB / "neuman.js").read_text()

    assert "function reconnectView(" in source
    assert "reconnectView(view)" in source
    assert "function connect(view)" in source
    # Not on the server's own refusal codes: reconnecting after 4401 would loop
    # against an expired session.
    assert "4401" in source and "4403" in source


def test_the_client_has_no_stale_element_references():
    """Every `$("id")` in the client must exist in the HTML.

    There is no DOM harness here, and the failure mode is silent: `$("policy")`
    after the element was renamed returns null, and the first property access
    throws inside an event handler where nothing surfaces it. N46 was this shape —
    no JavaScript had ever run and every server test passed.

    Crude, and the only thing that cannot go stale.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()

    used = set(re.findall(r'\$\("([a-z0-9-]+)"\)', script))
    assert used, "no element lookups found; this test would pass vacuously"

    declared = set(re.findall(r'id="([a-z0-9-]+)"', markup))
    missing = sorted(used - declared)
    assert not missing, f"the client looks up ids that no element declares: {missing}"


def test_the_client_has_no_shadowed_function_names():
    """One global scope means a redeclared function silently wins.

    `paintReasoning` is the composer chip's painter. The thinking renderer took
    the same name, so a reasoning frame called the chip painter — which happily
    repainted a control — while the thought itself stayed blank. Nothing threw:
    the later declaration wins without a warning, so a source test that searches
    for the thinking renderer's `function paintReasoning(row)` finds the first
    textual match and passes while the browser calls the chip painter. The
    client has no modules, so unique names are the only thing keeping these
    apart.
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())
    names = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)\(", code, re.M)
    assert names, "no top-level functions found; this test would pass vacuously"
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f"function names collide in one global scope: {duplicates}"


def test_the_settings_dialog_is_opened_directly_by_the_account_chip():
    """Settings has one opener, and it is the account chip itself.

    It was behind a two-item menu — Settings and Sign out — which put the thing
    people came for two clicks away. The chip opens the dialog now and Sign out
    moved into its sidebar, so the menu is gone. Asserted because the two halves
    are in different files: removing a surface without repointing the handler
    leaves a dead `$("account-menu")`, and leaving the menu while also wiring
    the chip is two ways to open one thing.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()

    assert 'id="account-toggle"' in markup, "the account chip is not in the client"
    assert 'id="settings"' in markup

    # The removed surfaces, in both files. A dead id in the script is a
    # TypeError on the first click rather than a layout bug.
    for gone in ("account-menu", "account-settings", "settings-toggle", "settings-foot"):
        assert gone not in markup, f"{gone} is still in the markup"
        assert gone not in script, f"the client still wires {gone}"

    # Sign out came with it, into the sidebar.
    assert 'id="logout"' in markup

    # The three panes the sidebar switches between, and the tab for each.
    for pane in ("interface", "permissions", "usage"):
        assert f'id="pane-{pane}"' in markup
        assert f'id="tab-{pane}"' in markup

    # Tools are deliberately *not* here any more: the composer's tool chip is
    # where they are seen and chosen (D50), and the old `#policy` list rendered
    # them a second time.
    assert 'id="policy"' not in markup
    assert "renderPolicy" not in script


def test_every_card_a_button_opens_says_whether_it_is_open():
    """`aria-expanded` lives on the control, and the list of controls is one list.

    A disclosure that never says it is open is invisible to anyone not using a
    mouse: the card appears from nowhere, and Escape — which dismisses it — looks
    like it did nothing. The attribute is written by an observer over `CARDS`
    rather than at each place a card is opened or hidden, because there are a dozen
    such places and the attribute set at the call site is the one a card added later
    will not get. What is worth asserting, then, is that the list and the markup
    agree — that is the half a new card can get wrong.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()

    match = re.search(r"const CARDS = \[(.*?)\];", script, re.S)
    assert match, "CARDS is gone; this test would pass vacuously"
    # Each entry is [panel, [opener, ...]]. A card may have more than one control
    # that opens it — the working directory is stated on the status line and
    # changed from the folder button — and every one of them is a disclosure.
    cards = [
        (panel, re.findall(r'"([\w-]+)"', openers))
        for panel, openers in re.findall(
            r'\[\s*"([\w-]+)"\s*,\s*\[([^\]]*)\]\s*\]', match.group(1)
        )
    ]
    assert len(cards) >= 5, f"only {len(cards)} cards in CARDS: {cards}"
    assert "MutationObserver" in script, (
        "nothing keeps aria-expanded in step, so every opening has to remember"
    )

    # Settings is deliberately *not* in `CARDS`: it is modal and its scrim is
    # what dismisses it, so the click-away loop must not also try. It is still a
    # disclosure, though — the chip toggles it — so the observer's list is
    # `CARDS` plus that pair, and it is the observer's list the markup must
    # agree with. Pinned so the two cannot drift apart silently.
    assert '["settings", ["account-toggle"]]' in script, (
        "the account chip opens Settings but is not in the aria-expanded observer"
    )
    assert not any(panel == "settings" for panel, _ in cards), (
        "Settings is in CARDS; the click-away loop would hide it and leave its scrim up"
    )

    aside = re.search(r'<aside id="settings"[^>]*>', markup)
    assert aside and 'role="dialog"' in aside.group(0)
    # Modal, and it says so: the scrim blocks what is behind it, so a reader
    # must not be told the rest of the page is still available.
    assert 'aria-modal="true"' in aside.group(0)

    for panel, openers in [*cards, ("settings", ["account-toggle"])]:
        assert openers, f"{panel} is in CARDS with no control that opens it"
        for button in openers:
            tag = re.search(rf'<button[^>]*id="{re.escape(button)}"[^>]*>', markup, re.S)
            assert tag, f"{button} is not in the markup, but it is said to open {panel}"
            assert "aria-expanded" in tag.group(0), (
                f"{button} opens {panel} and never says whether it is open"
            )


def test_the_working_directory_path_is_the_same_control_as_the_folder_button():
    """The path on the status line opens the chooser, like the folder button.

    The working directory is stated on the status line and changed from the
    composer's folder button. Two controls that mean the same thing have to share
    one handler and be one entry in `CARDS`: separate handlers drift apart, and an
    opener missing from `CARDS` is hidden by click-away on the way down and
    reopened by its own handler on the way up, so it can never close the menu.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()

    tag = re.search(r'<button[^>]*id="cwd-current"[^>]*>', markup, re.S)
    assert tag, "the working directory path is not a control"
    assert "aria-expanded" in tag.group(0), (
        "the path opens the chooser and never says whether it is open"
    )

    assert '$("cwd-pick").onclick = toggleWorkingDirMenu' in script
    assert '$("cwd-current").onclick = toggleWorkingDirMenu' in script, (
        "the path and the folder button have different handlers, or one has none"
    )

    cards = re.search(r"const CARDS = \[(.*?)\];", script, re.S)
    assert cards, "CARDS is gone; this test would pass vacuously"
    cwd = re.search(r'\["cwd-menu", \[([^\]]*)\]\]', cards.group(1))
    assert cwd, "the working directory chooser is not in CARDS"
    assert "cwd-current" in cwd.group(1), (
        "a press on the path would be treated as a press away and close the menu"
    )


def test_a_press_in_a_chooser_opens_and_the_row_button_takes():
    """A press opens; the row's own chip takes. Two actions, two targets.

    A press used to select, because it could not both select and navigate. The
    three panels gave the row a second job instead: the press opens the folder,
    the small chip on its right takes it, and the pointer — the only thing left
    that can point without acting — previews what it rests on. Neither verb may
    be wired straight into the row, or the chip and the press are the same act
    again, which is the bug this shape exists to avoid.
    """

    script = (WEB / "neuman.js").read_text()

    row = re.search(r"function chooserRow\(.*?\n  return row;\n\}", script, re.S)
    assert row, "chooserRow is gone; this test would pass vacuously"
    body = row.group(0)
    assert "row.onclick = () => openRow(menuId, path, kind)" in body, (
        "a press on a row does not open it"
    )
    assert "chooseRow(menuId, path)" in body and "event.stopPropagation()" in body, (
        "the chip does not take the row, or its press opens the row as well"
    )
    # The left panel's press is a step in the tree, which is what `descendTo` is;
    # the centre's must go through its two names, or the chip and the press are
    # the same act again.
    centre = body.split('if (role === "centre")')[1].split(
        'else if (role === "left" || role === "right")'
    )[0]
    assert not any(
        name in centre for name in ("descendTo(", "saveWorkingDir(", "attachFile(")
    ), "an action is wired straight into the row instead of through its two names"

    opened = re.search(r"function openRow\(.*?\n\}", script, re.S)
    taken = re.search(r"function chooseRow\(.*?\n\}", script, re.S)
    assert opened and taken, "the row's two verbs are gone; this test would pass vacuously"
    assert "descendTo(" in opened.group(0) and "attachFile(" in opened.group(0)
    assert "saveWorkingDir(" in taken.group(0) and "attachFile(" in taken.group(0)


def test_the_left_panel_shows_the_granted_roots_above_a_root():
    """A granted root has no parent in policy, and the panel above it still has a
    level.

    The menu opens at the working directory. At a root `parentWithinReach` is
    null, so the left panel went blank exactly at the top of the tree — the place
    a first visit starts. The level above a root is the other roots.
    """

    script = (WEB / "neuman.js").read_text()
    # `async` is optional: the function is synchronous today, and a test that
    # pins the declaration word fails on a change that does not touch the
    # behaviour it is here to protect.
    render = re.search(r"(?:async )?function renderChooser\(.*?\n\}", script, re.S)
    assert render, "renderChooser is gone; this test would pass vacuously"
    body = render.group(0)
    # The *left* target specifically. The centre's own target is `{ kind: "roots" }`
    # as well, so a test that only looked for that string passed while the left
    # panel was blank — the same self-agreeing shape as the fixtures in N152.
    above = re.search(r"const above = centre === null(.*?)\n\n", body, re.S)
    assert above, "the left panel's target is not computed where this test expects it"
    assert "parentWithinReach(centre)" in body, (
        "the panel above a directory no longer follows the directory"
    )
    assert "parent === null" in above.group(1) and '{ kind: "roots" }' in above.group(1), (
        "the left panel has no roots level, so it is blank at a granted root"
    )
    assert 'fillPanel(menuId, parts, view, "left", above)' in body, (
        "the computed level is not what the left panel is drawn from"
    )


def test_the_chooser_has_a_back_and_a_forward():
    """Up is not back: it cannot return to the sibling you were just in.

    The history is per open menu and one-way — a step taken *through* it must not
    be recorded, or Back would push the place it just left and Forward would
    never become available.
    """

    script = (WEB / "neuman.js").read_text()

    step = re.search(r"function stepHistory\(.*?\n\}", script, re.S)
    assert step, "stepHistory is gone; this test would pass vacuously"
    assert "descendTo(menuId, view.history[next]" in step.group(0), (
        "back and forward do not move the centre panel"
    )
    assert "false" in step.group(0), "a history step records itself, so Forward never enables"

    remember = re.search(r"function rememberPlace\(.*?\n\}", script, re.S)
    assert remember, "rememberPlace is gone; this test would pass vacuousally"
    assert "slice(0, view.at + 1)" in remember.group(0), (
        "navigating from a back-stepped place does not drop the forward entries"
    )

    crumbs = re.search(r"function paintCrumbs\(.*?\n\}", script, re.S)
    assert crumbs, "paintCrumbs is gone; this test would pass vacuously"
    # Escaped rather than literal: the arrows are what the client draws, and an
    # ambiguous-character lint rule is right to object to them in source.
    assert 'navButton("\u2039"' in crumbs.group(0) and 'navButton("\u203a"' in crumbs.group(
        0
    ), "the crumb bar draws no back and forward buttons"


def test_the_pointer_previews_and_never_takes():
    """With the press committed to opening, pointing is what previews.

    It has to be a *rest*, not a movement: the timer is armed once per row and
    only fires after the pointer settles, so sweeping the mouse across a
    directory dispatches nothing and takes nothing. And a preview may only ever
    select — the moment it can take something, the mouse is committing on its way
    past, which is the surprise this whole shape is meant to remove.
    """

    script = (WEB / "neuman.js").read_text()

    row = re.search(r"function chooserRow\(.*?\n  return row;\n\}", script, re.S)
    assert row, "chooserRow is gone; this test would pass vacuously"
    assert "row.onmousemove = () => hoverRow(menuId, row)" in row.group(0), (
        "the pointer does not preview the row it rests on"
    )

    hover = re.search(r"function hoverRow\(.*?\n\}", script, re.S)
    assert hover, "hoverRow is gone; this test would pass vacuously"
    body = hover.group(0)
    assert "setTimeout(" in body and "selectRow(menuId, row)" in body
    assert "view.hoverTimer" in body and "view.hoverRow === row" in body, (
        "the rest timer restarts on every pixel of movement, so it never settles"
    )
    assert not any(name in body for name in ("openRow(", "chooseRow(", "saveWorkingDir(")), (
        "a preview takes something; the pointer must only ever look"
    )


def test_the_arrows_take_the_caret_and_the_right_panel_is_a_place_to_go():
    """Two dead ends, one for each hand.

    A filter typed a moment ago kept its claim on Left and Right: the caret owned
    them even after Down had moved the selection into the list, so a list narrowed
    by typing could not be walked out of. Focus moves to the list on the first
    arrow, and a key typed from there is routed back into the field.

    The right panel's rows looked exactly like the left panel's and were the only
    rows in the menu that did nothing when pressed. Every panel is a place to go;
    a press on either side makes that row the centre.
    """

    script = (WEB / "neuman.js").read_text()
    markup = (WEB / "index.html").read_text()

    keydown = re.search(r"function chooserKeydown\(.*?\n\}", script, re.S)
    assert keydown, "chooserKeydown is gone; this test would pass vacuously"
    body = keydown.group(0)
    down = body.split('event.key === "ArrowDown" || event.key === "ArrowUp"')[1]
    down = down.split('event.key === "ArrowLeft"')[0]
    assert "parts.centre.focus({ preventScroll: true })" in down, (
        "the field keeps the caret after Down, so Left and Right keep moving it"
    )

    # The list has to be able to take that focus, or the call is a no-op.
    for ident in ("cwd-rows", "attach-rows"):
        tag = re.search(rf'<div id="{ident}"[^>]*>', markup, re.S)
        assert tag, f"{ident} is not in the markup"
        assert 'tabindex="-1"' in tag.group(0), f"{ident} cannot take focus from the field"

    row = re.search(r"function chooserRow\(.*?\n  return row;\n\}", script, re.S)
    assert row, "chooserRow is gone; this test would pass vacuously"
    sides = row.group(0).split('else if (role === "left" || role === "right")')
    assert len(sides) == 2, "the side panels' press is not where this test expects it"
    assert "openRow(menuId, path, kind)" in sides[1], (
        "a press on a row in the right panel does nothing"
    )
    assert 'if (role !== "centre") row.classList.add("pickable")' in row.group(0), (
        "the side panels' rows do not say they can be pressed"
    )


def test_the_card_is_one_height_and_the_chip_belongs_to_one_row():
    """Two things that stop the surface moving under the pointer.

    The card used to be `max-height`, so a two-row folder beside a ten-row preview
    dragged the whole box up and down as the selection changed. And a `use` chip
    on every line competes with the press that already opens the row, so it is
    drawn only for the row the pointer is over or the keyboard has selected —
    revealed by `visibility`, never `display`, because a chip that appears at the
    cost of reflowing the name beside it is the same jerk in a smaller place.
    """

    css = (WEB / "neuman.css").read_text()

    menu = re.search(r"#cwd-menu, #attach-menu \{(.*?)\n  \}", css, re.S)
    assert menu, "the chooser's box is gone; this test would pass vacuously"
    assert "height: min(340px, 38vh, var(--headroom-cap));" in menu.group(1), (
        "the card is still sized by its contents, so it will jerk as panels change"
    )
    assert "max-height" not in menu.group(1), (
        "a max-height leaves the card content-sized below the cap"
    )

    chip = re.search(
        r"#cwd-menu \.path-pick, #attach-menu \.path-pick \{(.*?)\n  \}", css, re.S
    )
    assert chip, "the row chip is gone; this test would pass vacuously"
    assert "visibility: hidden" in chip.group(1), "the chip is drawn on every row again"
    assert "display: none" not in chip.group(1), (
        "hiding the chip with `display` reflows the name on every hover"
    )
    assert ".path:hover .path-pick" in css and ".path.selected .path-pick" in css, (
        "the chip is not revealed for the row the pointer or the keyboard is on"
    )


def test_a_selection_is_kept_on_screen():
    """Arrowing past the panel's height must not walk the highlight out of sight.

    The panel scrolls and the keyboard does not, so the selection has to bring
    itself into view; otherwise the reader is choosing between things they cannot
    see, and Enter takes whatever the highlight landed on.
    """

    script = (WEB / "neuman.js").read_text()
    select = re.search(r"function selectRow\(.*?\n\}", script, re.S)
    assert select, "selectRow is gone; this test would pass vacuously"
    assert "reveal(parts.centre, row)" in select.group(0), (
        "the selection can leave the visible part of the list"
    )

    reveal = re.search(r"function reveal\(list, row\) \{.*?\n\}", script, re.S)
    assert reveal, "reveal is gone; this test would pass vacuously"
    assert "getBoundingClientRect()" in reveal.group(0) and "scrollTop" in reveal.group(0), (
        "the row is measured against the menu rather than the list that scrolls"
    )


def test_both_choosers_wear_the_accent_and_not_the_warning_hue():
    """Amber on a file picker read as a warning light; it is the composer's own
    surface, opened from the field it configures.

    `--warn` keeps its job where it means "careful, and reversible" — the waiting
    note, the approval's safe dot, the context meter. It just no longer paints the
    chooser's wash, edge or folder glyph.
    """

    css = (WEB / "neuman.css").read_text()

    assert "#cwd-menu, #attach-menu { --hue: var(--accent); }" in css, (
        "the chooser still wears the warning hue"
    )
    assert "#cwd-pick, #attach { --hue: var(--accent); }" in css, (
        "the composer's two chooser buttons still wear the warning hue"
    )


def test_a_selection_costs_one_preview_and_a_revisit_costs_none():
    """Three panels must not turn a picker into a dispatcher.

    Every listing is a dispatch to the acting user's client rather than a local
    read, so a preview that fired per keystroke would make arrowing through a
    directory cost more than the rest of this menu put together. The selection is
    debounced, and a directory already listed is answered from the open menu's
    cache — which is also what makes walking back out of a folder free.
    """

    script = (WEB / "neuman.js").read_text()

    preview = re.search(r"function schedulePreview\(.*?\n\}", script, re.S)
    assert preview, "schedulePreview is gone; this test would pass vacuously"
    assert "setTimeout(" in preview.group(0), (
        "the preview is not debounced, so holding an arrow key dispatches per row"
    )

    listing = re.search(r"function listDirectory\(.*?\n\}", script, re.S)
    assert listing, "listDirectory is gone; this test would pass vacuously"
    body = listing.group(0)
    assert "view.listings.get(path)" in body, "a directory already listed is fetched again"
    assert "view.inflight.get(path)" in body, (
        "two panels asking for the same directory would dispatch it twice"
    )


def test_the_crumb_path_keeps_its_leading_slash():
    """`direction: rtl` clips a long path's head — and moves a short path's slash.

    The trick is what makes a deep path show its tail, and it has to stay: on a
    path that fits, though, the bidi algorithm moves the leading `/` to the far
    end, so the crumb read `home/alice/projects/`. Wrapping the text in a
    left-to-right run keeps both properties.
    """

    script = (WEB / "neuman.js").read_text()
    css = (WEB / "neuman.css").read_text()

    assert "direction: rtl" in css, (
        "the left-clipping rule is gone, so a deep path now hides its own tail"
    )
    crumbs = re.search(r"function paintCrumbs\(.*?\n\}", script, re.S)
    assert crumbs, "paintCrumbs is gone; this test would pass vacuously"
    assert 'el("bdi",' in crumbs.group(0) and 'text.dir = "ltr"' in crumbs.group(0), (
        "the crumb is a single RTL run, so a fitting path loses its leading slash"
    )


def test_escape_dismisses_every_card_a_button_opens():
    """Escape dismissed one card of six before this.

    It closed the Models window and nothing else, so the sessions list, Settings and
    both path choosers could be dismissed by clicking the transcript and by no key
    at all — the one dismissal a keyboard user has, missing on most of them.

    Asserted against the shared list rather than against the cards by name: a card
    added later must not need a second edit here to become dismissable, and the way
    to guarantee that is to check the handler walks `CARDS` rather than to enumerate
    what it should have walked.
    """

    src = WEB / "neuman.js"
    script = src.read_text()

    handlers = [
        block
        for block in re.findall(
            r'document\.addEventListener\("keydown", \(event\) => \{(.*?)\n\}\);',
            script,
            re.S,
        )
        if '"Escape"' in block
    ]
    assert handlers, "no Escape handler found; this test would pass vacuously"
    escape = "\n".join(handlers)

    assert "of CARDS" in escape, "Escape does not walk the shared list of cards"
    assert "cwd-menu" in escape and "attach-menu" in escape, (
        "Escape must yield to the choosers, which own their own dismissal"
    )
    assert "rowMenu" in escape, "the row menu floats above every card, so Escape takes it first"
    # Settings is modal, so nothing behind it is reachable and Escape can only
    # mean the dialog. It is handled before the rail and the cards, and through
    # `closeSettings` rather than by hiding the panel — hiding it alone would
    # leave the scrim swallowing every click afterwards.
    assert "closeSettings()" in escape, (
        "Escape does not close the settings dialog, or closes it without its scrim"
    )
    # Returning focus to the chip is `closeSettings`'s job now rather than this
    # handler's, since every dismissal — Escape, the scrim, the ✕ — has to do
    # it. Asserted there instead of here: a ring left on a hidden dialog is
    # invisible, and the keyboard's round trip ends nowhere.
    close = re.search(r"function closeSettings\(\) \{(.*?)\n\}", script, re.S)
    assert close, "closeSettings is gone; this test would pass vacuously"
    assert 'account-toggle").focus()' in close.group(1), (
        "dismissing Settings does not return focus to the chip that opened it"
    )


def test_typing_anywhere_reaches_the_composer():
    """The composer takes typing while the window has focus, not only after a click.

    Clicking and highlighting the transcript, scrolling it, opening a tool card —
    every one of those moves focus off the box, and every one used to mean a
    second click before the next sentence. The keystroke is not consumed: focus
    moves and the browser's own insertion lands the character at the caret. So the
    half worth asserting is the exclusions, because an over-eager listener of this
    shape is one that eats a rename box, an IME composition, or a keyboard command.
    """

    src = WEB / "neuman.js"
    script = src.read_text()

    handlers = [
        block
        for block in re.findall(
            r'document\.addEventListener\("keydown", \(event\) => \{(.*?)\n\}\);',
            script,
            re.S,
        )
        if '("input").focus()' in block
    ]
    assert len(handlers) == 1, f"expected one type-anywhere handler, found {len(handlers)}"
    typing = handlers[0]

    assert "function takesText" in script, "the editable-field check is not defined"
    assert "takesText(event.target)" in typing, (
        "the listener would steal a keystroke from a rename box, a chooser filter, "
        "or an already-focused composer"
    )
    assert all(k in typing for k in ("event.ctrlKey", "event.metaKey", "event.altKey")), (
        "a modifier keystroke is a command, not a character"
    )
    assert '"v"' in typing and "paste" in typing, (
        "Cmd/Ctrl+V is input: pasting into the transcript must reach the composer"
    )
    assert "event.isComposing" in typing, "an IME composition must be left to the IME"
    assert "activates(event.target)" in typing, (
        "Space over a menu button must still mean press, not a space in the box"
    )
    assert "cwd-menu" in typing and "attach-menu" in typing, (
        "the chooser owns its keyboard; typing there must not jump to the composer"
    )
    assert "settingsOpen()" in typing, (
        "the settings dialog is modal, so focusing the composer behind its scrim "
        "puts the caret somewhere the reader cannot see and loses the keystroke"
    )
    assert "composer" in typing, (
        "the login surface has a hidden composer, so typing there would land nowhere"
    )
    assert "preventDefault" not in typing, (
        "the browser's own insertion is what puts the character in at the caret"
    )


def test_the_composer_arrows_review_prompts_without_touching_the_draft():
    """Up and Down walk the user's messages; a draft keeps its own caret.

    The composer is multi-line, so stealing Up unconditionally would break caret
    movement in a draft. The first press is gated on the edge the arrow points
    at; after that the cursor owns the arrows, the textarea is never read or
    written, and Down past the newest returns to the tail. A streamed token must
    not drag the reader off the message mid-review, so the cursor clears
    `followTail` and the `jump-live` arrow is the way back.
    """

    src = WEB
    code = re.sub(r"//.*", "", (src / "neuman.js").read_text())

    start = code.find('$("input").onkeydown')
    assert start != -1, "the composer keydown handler is gone; this test would pass vacuously"
    end = code.find("\n};", start)
    assert end != -1, "the composer keydown handler is never closed"
    body = code[start:end]

    assert "ArrowUp" in body and "ArrowDown" in body, "the arrows do not review the prompts"
    assert "selectionStart === 0" in body, (
        "the first Up is not gated on the caret, so a draft loses line navigation"
    )
    assert "event.preventDefault()" in body, "the arrows still move the caret mid-review"
    assert "box.value" not in body, "reviewing the prompts edits the draft"
    assert "promptMessages()" in body, "the cursor does not read the user messages"

    show = re.search(r"function showPrompt\(node\)\s*\{(.*?)\n\}", code, re.S)
    assert show, "showPrompt is gone; this test would pass vacuously"
    assert "followTail = false" in show.group(1), (
        "a streamed token will drag the reader off the message being reviewed"
    )
    assert "scrollIntoView" in show.group(1), "the reviewed prompt is not brought on screen"

    end = re.search(r"function endPromptCursor\(\)\s*\{(.*?)\n\}", code, re.S)
    assert end, "endPromptCursor is gone; this test would pass vacuously"
    assert "followTail = true" in end.group(1), "leaving the review does not return to the tail"

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", code, re.S)
    assert replay, "replay is gone; this test would pass vacuously"
    assert "promptCursor = null" in replay.group(1), (
        "a new transcript keeps a cursor into the transcript it replaced"
    )

    styles = (src / "neuman.css").read_text()
    assert ".turn.user.current" in styles, "the reviewed prompt carries no mark"


def test_the_transcript_can_actually_scroll():
    """`overflow-y: auto` on a flex child does nothing without `min-height: 0`.

    A flex item's `min-height` defaults to `auto`, which in a column flexbox means
    "never shrink below your content" — so `#log` was always exactly as tall as the
    transcript, the *page* overflowed instead of the log, and the composer and any
    Approve button were pushed off the bottom of the viewport. The user's symptom was
    tabbing blindly to reach Approve, or zooming out until it appeared.

    Asserted against the stylesheet because there is no layout engine here, and the
    property is one declaration whose absence is silent.
    """

    css = (WEB / "neuman.css").read_text()

    match = re.search(r"#log\s*\{(.*?)\}", css, re.S)
    assert match, "#log has no rule block; this test would pass vacuously"
    # Comments stripped first: the explanation above the declaration names the
    # property, so a substring search matched the prose and passed against a
    # stylesheet with the declaration deleted.
    block = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)

    assert re.search(r"^\s*overflow-y:\s*auto\s*;", block, re.M), block
    assert re.search(r"^\s*min-height:\s*0\s*;", block, re.M), (
        "#log scrolls only if it may be shorter than its content: `flex: 1` sets "
        "flex-basis but leaves min-height: auto, so the log cannot shrink and the "
        "page overflows instead"
    )


def test_the_page_never_scrolls_sideways():
    """A wide row must not raise a page-wide scrollbar and clip the right edge.

    An in-flow element wider than the window gave the page a horizontal
    scrollbar; the scrollbar made `height: 100vh` a hair taller than the visible
    area, so a vertical scrollbar appeared and the fixed controls on the right —
    the account circle and the rail grip — were half-covered. The root clips
    horizontally and takes its height from the viewport rather than `100vh`, and
    the transcript clips its own width; wide content scrolls in its own card.
    """

    styles = (WEB / "neuman.css").read_text()

    html = re.search(r"\n  html \{([^}]*)\}", styles)
    assert html, "no html rule; this test would pass vacuously"
    assert "overflow-x: clip" in html.group(1), (
        "the page can still raise a horizontal scrollbar"
    )
    assert "height: 100%" in html.group(1)

    body = re.search(r"\n  body \{([^}]*)\}", styles, re.S)
    assert body, "no body rule; this test would pass vacuously"
    assert "height: 100%" in body.group(1), (
        "body still uses 100vh, so a scrollbar makes it taller than the viewport"
    )
    assert "height: 100vh" not in body.group(1)

    log = re.search(r"#log\s*\{([^}]*)\}", styles)
    assert log, "no #log rule; this test would pass vacuously"
    assert "overflow-x: hidden" in log.group(1), (
        "a wide transcript row can still scroll the page sideways"
    )


def test_the_stylesheets_comments_are_closed_exactly_once():
    """A stray `*/` turns the prose after it into declarations.

    This stylesheet carries more comment than code by design, and the paragraphs
    are long enough that an edit can leave a `*/` mid-block. The cost is not a
    cosmetic one: CSS error recovery discards to the next `}`, so the prose is
    parsed as a declaration and the *rule it was documenting* can go with it.
    A rule dropped that way fails silently and only in the browser — nothing
    here reads the stylesheet, so no other test would notice.

    Counting delimiters rather than parsing: there is no CSS parser in the
    closure, and unbalanced `/*` / `*/` is the whole failure mode.
    """

    css = (WEB / "neuman.css").read_text()

    # CSS comments do not nest, so every `/*` takes exactly one `*/`. A string or
    # `url()` containing either sequence would confuse this, and there is none —
    # which is worth knowing if this ever fires on a line that looks innocent.
    assert css.count("/*") == css.count("*/"), (
        f"{css.count('/*')} comment openers against {css.count('*/')} closers: "
        "an unclosed or doubly-closed comment drops whatever rule follows it"
    )

    # Ordering, which the counts alone do not catch: `*/ … /*` balances and still
    # leaves prose outside a comment. Walk the delimiters in the order they
    # appear and require them to alternate, opener first.

    expected = "/*"
    for match in re.finditer(r"/\*|\*/", css):
        found = match.group()
        line = css.count("\n", 0, match.start()) + 1
        assert found == expected, (
            f"neuman.css:{line}: found `{found}` where `{expected}` was due — "
            "the prose around it is being parsed as CSS"
        )
        expected = "*/" if found == "/*" else "/*"
    assert expected == "/*", "neuman.css ends inside an unclosed comment"


def test_upward_panels_are_bounded_by_the_room_above_the_composer():
    """A card opening upward must be capped by measured headroom, not by `vh`.

    This guarded two full-height side columns once, then the `vh` caps on the
    cards that replaced them. A fraction of the viewport is the wrong bound and
    was the bug (N134): the caps assumed the composer sits on the floor of the
    window, and in the draft it is centred, so a panel allowed 60vh of a ~50vh
    gap ran off the top with its first rows unreachable.

    So the assertion is on the bound itself. Every panel positioned off
    `#context`'s top edge has to consult `--headroom-cap`, which the client
    measures; a new one that reaches for a `vh` number instead is the regression,
    and it is invisible until someone opens it on the new-session screen.
    """

    css = (WEB / "neuman.css").read_text()
    script = (WEB / "neuman.js").read_text()

    # The cap is derived from the measurement, and floored so that a window too
    # short to satisfy it opens a scrollable sliver rather than nothing: a
    # `max-height` cannot go negative, it clamps to zero, and a panel clamped to
    # zero reads as a dead chip.
    cap = re.search(r"--headroom-cap:\s*([^;]+);", css)
    assert cap, "no --headroom-cap; the upward panels have no measured bound"
    assert "var(--headroom" in cap.group(1), (
        "--headroom-cap ignores the measurement it exists to carry"
    )
    assert "max(" in cap.group(1), (
        "--headroom-cap has no floor, so a short window collapses the panels"
    )

    # The measurement itself. A `resize` listener would miss the case that
    # produced N134 — in the draft the card is centred, so the window getting
    # shorter moves its top edge without changing its own box.
    assert "--headroom" in script and "ResizeObserver" in script, (
        "nothing publishes --headroom, so the cap falls back to its default"
    )
    assert "getBoundingClientRect" in script, (
        "--headroom is not measured from the composer's actual top edge"
    )

    # A `ResizeObserver` fires on size and not position, so toggling `drafting` —
    # the composer moving from the floor of the window to the middle of it —
    # changes no box anywhere and leaves `--headroom` at the previous layout's
    # value. That is the N138 case, and it looks exactly like N134: the tools
    # panel opens past the top with its first rows unreachable. The two places
    # that toggle the class ask for the measurement directly.
    assert "function measureHeadroom(" in script, (
        "the measurement is anonymous, so nothing can ask for it on a move"
    )
    for fn in ("paintHero", "leaveDraft"):
        body = re.search(rf"function {fn}\([^)]*\)\s*\{{(.*?)\n\}}", script, re.S)
        assert body, f"{fn} is gone; this test would pass vacuously"
        assert "measureHeadroom()" in body.group(1), (
            f"{fn} moves the composer without re-measuring the room above it"
        )

    # Every rule that opens off `#context`'s top edge, and the bound each one
    # applies. `bottom: 100%` is the shared tell: it is what puts a box above the
    # composer and into the space this cap describes. Comments go first, or a
    # prose mention of the anchor is read as a selector.
    bare = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    upward = [
        (selector.strip(), body)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", bare)
        if "bottom: 100%" in body and "position: absolute" in body
    ]
    assert len(upward) >= 5, f"expected the upward cards, found {upward}"

    # The hero is the exception, and it is one by *kind*: it is the draft's
    # heading, a line of text with nothing to scroll, so a height cap would do
    # nothing for it. Named rather than filtered by shape, so a real panel cannot
    # slip through by resembling it.
    for selector, body in upward:
        if selector == "body.drafting .hero":
            continue
        assert "--headroom-cap" in body, (
            f"{selector} opens upward with no headroom bound: on the "
            "new-session screen it will run off the top of the window"
        )


def test_following_the_tail_is_read_before_the_element_grows():
    """`grow` must read the reader's intent before the mutation, never after.

    The intent is a flag rather than a measurement (N141): a distance test compares
    where the reader is against where the content ends, and streaming moves the
    second, so a wheel tick smaller than the slack was undone by the next frame.
    The flag closes that, but only while it is *read* first — a `followTail` consulted
    after `mutate()` can have been cleared by a listener that fired on the write's own
    layout, which reintroduces N141 through the back door. Ordering is the invariant,
    and ordering is exactly what a later edit loses.
    """

    script = (WEB / "neuman.js").read_text()

    match = re.search(r"function grow\(mutate\)\s*\{(.*?)\n\}", script, re.S)
    assert match, "grow() not found; this test would pass vacuously"
    body = re.sub(r"//.*", "", match.group(1))

    assert "atTail()" not in body, (
        "grow() is measuring distance again; N141 is that distance cannot "
        "distinguish a reader who scrolled up from content that just grew"
    )
    read = body.index("followTail")
    mutate = body.index("mutate()")
    assert read < mutate, (
        "grow() reads followTail after mutating; a listener firing on the write's "
        "own layout can clear it first, and the reader is pinned to the tail again"
    )


def test_every_transcript_mutation_follows_the_tail():
    """Anything that changes the transcript's height has to scroll it.

    This is the bug the user reported twice before it was understood. A card was
    created short — a name and its arguments — and then grew: the approval buttons
    appeared, the state changed, the output was appended. Those were direct
    `card.appendChild` and `textContent` writes on an element already in the log, so
    they never went through the append path and the page never scrolled. The
    transcript only caught up when the next model token arrived, leaving Approve
    buttons below the fold with no way to reach them.

    The card no longer grows the transcript — its body scrolls inside a fixed
    height (D62) — but the row and the head are still written after mount, and a
    future shape that does grow must not bypass the tail. So the invariant stays:
    "anything that changes the height of something already displayed scrolls".
    The result's own half is checked too: it lands inside the card's scrollport,
    so it follows that box's tail rather than the transcript's.

    Checked by looking at each place a *live* card is fetched and then written to —
    building a card before it is added cannot scroll and does not need to, which is
    what a first version of this test got wrong.
    """

    script = (WEB / "neuman.js").read_text()
    # Comments name these patterns while explaining them, so strip them first — the
    # mistake N72's test made.
    code = re.sub(r"//.*", "", script)

    assert "function grow(" in code, "grow() is gone; height changes no longer scroll"

    # Only the *live* frame handlers. `replay` rebuilds a whole transcript from
    # history and scrolls once at the end, which is correct and is not what this is
    # about — a first version of this test flagged it, which would have pushed a
    # pointless `grow()` into a loop that runs hundreds of times.
    handler = code[code.index("function handle(") :]
    sites = [m.end() for m in re.finditer(r"toolCards\.get\(", handler)]
    assert sites, "no toolCards.get sites in the frame handler; test would be vacuous"

    for offset in sites:
        window = handler[offset : offset + 900]
        # Any write to the retrieved card: appending a child or rewriting its state.
        if "card.appendChild" not in window and "card.querySelector" not in window:
            continue
        assert "grow(" in window, (
            "a live tool card is mutated without grow(), so the transcript will not "
            f"follow it: ...{window[:120].strip()}..."
        )

    # The card's state is written through `setToolState`, which updates the detail
    # and the timeline row together so the two cannot disagree. Its call sits
    # *inside* the `grow()` that wraps it, so the wrapper is already open by the
    # time the call appears and the search is backwards.
    state_sites = [m.start() for m in re.finditer(r"setToolState\(", handler)]
    assert state_sites, "no setToolState sites in the frame handler; vacuous"
    for start in state_sites:
        before = handler[max(0, start - 400) : start]
        assert "grow(" in before, (
            "a live tool state is written outside grow(), so the transcript will "
            f"not follow it: ...{handler[start : start + 120].strip()}..."
        )

    # The result is appended inside the card's own scrollport, not to the
    # transcript, so it follows that box. A `renderResult` called bare would
    # leave the output below the fold inside the card.
    result = re.search(r'case "tool_result":\s*\{(.*?)\n    \}', code, re.S)
    assert result, "the tool_result handler is gone; this test would pass vacuously"
    assert 'card.querySelector(".tool-body")' in result.group(1), (
        "the result is not written into the card's scrolling body"
    )
    assert "growInner(body" in result.group(1), (
        "a result that lands below the card's fold does not follow the card's tail"
    )


def test_a_replayed_tool_card_shows_its_outcome_not_a_placeholder():
    """A reopened session shows what each call did, not that it happened.

    Live cards move through `waiting for you` → `running · auto` → the result, and
    none of those transitions are in the stored history — the display projection
    carries the tool result and its `exit_code` instead. The card used to be
    stamped `from this session`, which said only what the transcript already
    showed. Now the head says `failed` when it did, and the row carries the
    result's size and an outcome for its node, so a reopened session reads the
    same as the live one it replaced (D44).
    """

    script = (WEB / "neuman.js").read_text()
    code = re.sub(r"//.*", "", script)

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", code, re.S)
    assert replay, "replay is gone; this test would pass vacuously"
    body = replay.group(1)
    assert "from this session" not in body, (
        "the placeholder is back on every replayed tool card"
    )
    assert "message.exit_code" in body, (
        "a replayed card does not read the outcome the projection carries"
    )
    assert 'failed ? "failed" : ""' in body, (
        "a replayed card shows an exit code (or nothing) instead of `failed`"
    )
    assert "humanBytes(message.content)" in body, (
        "a replayed row does not say how much came back"
    )
    assert 'failed ? "failed" : "done"' in body, (
        "a replayed row does not colour its node by outcome"
    )


def test_a_replayed_turn_shows_its_chain_of_thought():
    """A reopened session shows the thinking a live turn showed.

    Reasoning is not in what the model is sent back for most dialects, so the
    display projection used to omit it and a reopened session quietly lost every
    thinking row the reader had watched. It now rides the display projection under
    a reader-only `reasoning` field, and `replay` draws it with the same
    `reasoningBlock` the live path uses, so the two views agree.
    """

    script = (WEB / "neuman.js").read_text()
    code = re.sub(r"//.*", "", script)

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", code, re.S)
    assert replay, "replay is gone; this test would pass vacuously"
    body = replay.group(1)
    assert "if (message.reasoning)" in body, "a replayed turn drops its chain of thought"
    assert "reasoningBlock({ animate: false })" in body, (
        "the replayed thought is not the same thinking row, without the live arrival"
    )
    assert "_preview.textContent = previewLine(plainPreview(message.reasoning))" in body, (
        "the replayed thinking row has no plain preview"
    )
    assert "renderMarkdown(message.reasoning, row._body)" in body, (
        "the replayed thinking body is not drawn as markdown"
    )


def test_a_replayed_compaction_renders_the_note():
    """The display projection marks where the model's context was shortened.

    The reader's transcript keeps the messages a compaction folded, so it needs
    the marker that explains why the model behaves as though it had forgotten
    them — the same line the live `compacted` frame draws (N129). A `note` message
    the client ignores would be worse than none, so `replay` has to draw it.
    """

    code = re.sub(
        r"//.*",
        "",
        (WEB / "neuman.js").read_text(),
    )

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", code, re.S)
    assert replay, "replay is gone; this test would pass vacuously"
    body = replay.group(1)
    assert 'message.role === "note"' in body, "the compaction marker is not drawn"
    assert 'el("div", "turn note", message.content)' in body, (
        "the compaction marker is not the same note the live frame draws"
    )


def test_a_finished_tool_says_failed_rather_than_an_exit_code():
    """The card's head says `failed`, or the specific reason, and never a number.

    `exit 0` on every completed card is noise and `exit 1`/`-1`/`2` is a number
    most readers cannot interpret at a glance; the failure is already in the result
    body, in red. So a finished success says nothing on the card, a failure says
    `failed`, and a timeout or cancellation keeps its more specific word. The
    timeline row is where the result lands: its size on success, nothing when
    there is no body, and its node carries the outcome — the live states
    (`waiting for you`, `running`) stay in both, because a pending call is exactly
    what a one-line glance is for (D44).
    """

    script = (WEB / "neuman.js").read_text()
    code = re.sub(r"//.*", "", script)

    # `setToolState` is the one writer, and it takes the row form and the node's
    # outcome separately.
    writer = re.search(r"function setToolState\(([^)]*)\)", code)
    assert writer, "setToolState is gone; this test would pass vacuously"
    assert "rowText" in writer.group(1), "setToolState cannot write a different row form"
    assert "outcome" in writer.group(1), "setToolState cannot colour the node"

    result = re.search(r'case "tool_result":\s*\{(.*?)\n    \}', code, re.S)
    assert result, "the tool_result handler is gone; this test would pass vacuously"
    handler = result.group(1)
    assert "exit ${frame.exit_code}" not in handler, "the exit code is back on the card"
    assert '"failed"' in handler, "a failed call does not say `failed`"
    assert '"timed out"' in handler and '"cancelled"' in handler, (
        "a timeout or cancellation lost its more specific word"
    )
    assert "humanBytes(frame.content)" in handler, (
        "the timeline row does not say how much came back"
    )
    assert "(frame.tool_call_id, outcome, summary, kind)" in handler, (
        "the row is not given the result and the outcome together"
    )


def test_the_answer_is_text_and_the_user_bubble_is_a_box():
    """The agent's reply has no surface; the user's does.

    The transcript is the page, and a panel behind every answer made the
    conversation read as a feed of objects. The user bubble stays — one voice
    interrupting another has to be findable on the way back up — but the answer is
    just the words, in the measure the transcript already centers. Only the outer
    box goes: fenced code, inline code and every other markdown surface keep their
    own, because those are places a reader distinguishes *within* the reply.
    """

    styles = (WEB / "neuman.css").read_text()
    code = re.sub(r"/\*.*?\*/", "", styles, flags=re.S)

    agent = re.search(r"\.turn\.agent\s*\{([^}]*)\}", code)
    assert agent, ".turn.agent is gone; this test would pass vacuously"
    body = agent.group(1)
    assert "background" not in body, "the answer has a surface of its own again"
    assert "border" not in body, "the answer is boxed again"

    user = re.search(r"\.turn\.user\s*\{([^}]*)\}", code)
    assert user and "background" in user.group(1), (
        "the user bubble lost the surface that separates it from the answer"
    )

    # The markdown code surfaces are not swept up with the outer box.
    assert ".md .md-code" in code, "fenced code lost its own surface"


def test_a_thinking_card_renders_the_markdown_it_was_written_in():
    """A thought is prose, and is read rather than watched.

    The chain of thought arrives as markdown — models write `**bold**`, lists,
    headings and fences whether or not anything renders them — but the thinking
    body was a `textContent` sink, so a thought read as its own syntax while the
    reply beside it was formatted. It then re-rendered from source every frame,
    which is the right rate for a crossfade and the wrong one for prose: sixty
    updates a second is more than the eye can parse.

    So the live card is fed on a reading clock — whole-word chunks, one every
    200ms — and appends, because a renderer that clears its container replays the
    bloom on everything already read. The settled card is drawn once by the same
    renderer a reply uses, which is where lists, fences and tables appear. A
    reopened session fills the body that way when the row is first opened.
    """

    script = (WEB / "neuman.js").read_text()
    code = re.sub(r"//.*", "", script)

    block = re.search(r"function reasoningBlock\([^)]*\)\s*\{(.*?)\n\}", code, re.S)
    assert block, "reasoningBlock is gone; this test would pass vacuously"
    assert 'el("div", "reasoning-body md")' in block.group(1), (
        "the thinking body does not carry the markdown scope"
    )
    assert "row._follow = true" in block.group(1), (
        "a streamed thought no longer follows its own tail"
    )

    paint = re.search(r"function paintThought\(row\)\s*\{(.*?)\n\}", code, re.S)
    assert paint, "paintThought is gone; this test would pass vacuously"
    assert "plainPreview(raw)" in paint.group(1), (
        "the row preview shows markdown marks instead of the thought"
    )
    assert "THOUGHT_CHUNK_MS" in paint.group(1), (
        "the live thought paints on the rendering clock again instead of the reading one"
    )

    settle = re.search(r"function settleThought\(row\)\s*\{(.*?)\n\}", code, re.S)
    assert settle, "settleThought is gone; this test would pass vacuously"
    assert "renderMarkdown(raw, row._body)" in settle.group(1), (
        "a finished thought is never drawn as block markdown"
    )

    append = re.search(r"function appendThought\(row, text\)\s*\{(.*?)\n\}", code, re.S)
    assert append, "appendThought is gone; this test would pass vacuously"
    assert "inlineMarkdown(piece, chunk)" in append.group(1), (
        "a chunk is not rendered as inline markdown"
    )
    assert "renderMarkdown" not in append.group(1), (
        "the live thought rebuilds its body, which replays the bloom on every chunk"
    )

    follow = re.search(r"function followThought\(row, now\)\s*\{(.*?)\n\}", code, re.S)
    assert follow, "followThought is gone; this test would pass vacuously"
    assert "Math.exp" in follow.group(1), (
        "the follow is a per-frame constant, so it moves at a different speed on a "
        "display that refreshes at a different rate"
    )

    frame = re.search(r'case "reasoning":(.*?)break;', _handle(code), re.S)
    assert frame, "the reasoning frame handler is gone; this test would pass vacuously"
    body = frame.group(1)
    assert "_body.dataset.raw" in body, "the streamed thought is not kept as markdown source"
    assert "paintThought(reasoningTurn)" in body, (
        "the reasoning frame bypasses the reading clock"
    )

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", code, re.S)
    assert replay, "replay is gone; this test would pass vacuously"
    assert "renderMarkdown(message.reasoning, row._body)" in replay.group(1), (
        "a reopened session draws its thinking as raw markdown"
    )

    styles = re.sub(r"/\*.*?\*/", "", (WEB / "neuman.css").read_text(), flags=re.S)
    thought = re.search(r"\.reasoning-body\s*\{([^}]*)\}", styles)
    assert thought, ".reasoning-body is gone; this test would pass vacuously"
    assert "font-style: italic" not in thought.group(1), (
        "the whole thought is italic again, so emphasis inside it cannot show"
    )
    assert ".reasoning-body p.live" in styles, "the live paragraph no longer carries full ink"
    assert ".reasoning-body .md-chunk" in styles, "a chunk no longer blooms in"
    assert ".reasoning-body.scrolls" in styles and "mask-image" in styles, (
        "the reading band is gone, or no longer waits for the body to overflow"
    )


def test_markdown_renders_tables_as_tables():
    """A markdown table is a grid, not a paragraph of pipes.

    `renderMarkdown` handled fences, headings, lists and quotes but had no table
    rule, so a reply's `| Name | Value |` block arrived as prose with its pipes
    intact — the one markdown surface the answer was still showing as syntax.
    The delimiter row is what distinguishes a table from a paragraph that
    contains a `|`, and alignment is a class because the CSP forbids inline
    styles. The wrapper scrolls, because the document never does (N132).
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())
    render = re.search(r"function renderMarkdown\(text, into\)\s*\{(.*?)\n\}", code, re.S)
    assert render, "renderMarkdown is gone; this test would pass vacuously"
    body = render.group(1)
    for needle in (
        "tableCells(line)",
        'el("table", "md-table")',
        'el("thead")',
        'el("tbody")',
        'el("div", "md-table-wrap")',
        "tableAlign(marks[k])",
    ):
        assert needle in body, f"the table renderer lost {needle}"

    cells = re.search(r"function tableCells\(line\)\s*\{(.*?)\n\}", code, re.S)
    assert cells, "tableCells is gone; this test would pass vacuously"
    escaped = 'text[k] === "\\\\"'
    assert escaped in cells.group(1), "an escaped pipe is split as a cell boundary"

    styles = re.sub(r"/\*.*?\*/", "", (WEB / "neuman.css").read_text(), flags=re.S)
    assert ".md .md-table-wrap" in styles, "a wide table can widen the document"
    assert ".md .md-table th, .md .md-table td" in styles, "the table has no cell rule"
    assert "text-align: center" in styles and "text-align: right" in styles, (
        "the delimiter row's alignment is not drawn"
    )


def test_fenced_code_is_highlighted_when_the_fence_names_a_language():
    """A fenced block gets the four token classes, but only where they fit.

    The file view has highlighted code since it was written; a reply's fence was
    a plain `pre`, so the same code was coloured in a tool card and flat in the
    answer beside it. The highlighter is four regex classes, not a grammar, so a
    fence is coloured only when it names a language that highlighter knows — a
    `diff` or a `text` block stays plain rather than being confidently wrong.
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())

    assert "const HIGHLIGHT_LANGS" in code, "the fence language allow-list is gone"
    render = re.search(r"function renderMarkdown\(text, into\)\s*\{(.*?)\n\}", code, re.S)
    assert render, "renderMarkdown is gone; this test would pass vacuously"
    body = render.group(1)
    assert "HIGHLIGHT_LANGS.has(language)" in body, "a fence is coloured without asking"
    assert "highlight(lineText, pre)" in body, "a known fence language is not highlighted"

    styles = re.sub(r"/\*.*?\*/", "", (WEB / "neuman.css").read_text(), flags=re.S)
    for token in ("c", "s", "n", "k"):
        assert f".md .md-code .{token}" in styles, f"fenced code's .{token} token is unstyled"


def test_the_work_trace_is_set_apart_from_the_prose():
    """The run is a step back from the answer, in space and in ink.

    The timeline has two registers: the agent's prose, which is what the reader
    came for, and the work run — calls and thinking — which is the trace that
    produced it. They were the same white at the same edge, so a turn read as one
    flat column. The run is now inset from the measure on both sides (D44) and its
    subjects use `--work`, a neutral between `--text` and `--dim`; an opened card
    keeps `--text`, because there it is the content rather than a trace.
    """

    styles = (WEB / "neuman.css").read_text()
    css = re.sub(r"/\*.*?\*/", "", styles, flags=re.S)

    palette = (WEB / "palette.css").read_text()
    assert "--work:" in palette, "the trace has no ink of its own"
    row = re.search(r"\.tool-row \.subject\s*\{([^}]*)\}", css)
    assert row and "var(--work)" in row.group(1), "the row subject is still the prose ink"
    card = re.search(r"\.tool \.subject\s*\{([^}]*)\}", css)
    assert card and "var(--text)" in card.group(1), (
        "the opened card's subject lost the reading ink"
    )
    run = re.search(r"\.work-run\s*\{([^}]*)\}", css)
    assert run and "margin-inline" in run.group(1), "the run is not set in from the prose"


def test_a_call_verb_is_blue_and_a_thought_label_is_dim():
    """A call and a thought are told apart by colour, not only by their nodes.

    With the audit name gone the verb is what names the act, and it wears the
    accent so a run of calls scans as calls; a thinking row keeps the dim
    `thinking` label in the same gutter, so the one row that is not a call does
    not read as another verb. Both are in the same column, which is what makes the
    difference a colour rather than a position.
    """

    styles = (WEB / "neuman.css").read_text()
    css = re.sub(r"/\*.*?\*/", "", styles, flags=re.S)

    verb = re.search(r"\.tool-row \.verb\s*\{([^}]*)\}", css)
    assert verb and "var(--accent)" in verb.group(1), "a call's verb is not the accent"
    label = re.search(r"\.think-row \.label\s*\{([^}]*)\}", css)
    assert label and "var(--dim)" in label.group(1), (
        "the thinking label is no longer dim, so it reads as a call"
    )


def test_token_counts_are_not_in_the_transcript():
    """A running total is not an event in the conversation.

    Usage arrived as a `note` after every turn, so the log filled with lines that are
    only ever their latest value — and each one pushed the actual exchange further up,
    which made the scroll problem worse. It belongs in a status line below the
    composer.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()

    assert 'id="usage"' in markup, "no status element for token counts"
    assert "note(usageLine" not in script, (
        "usage is still written into the transcript as a note"
    )
    assert '$("usage").textContent = usageLine' in script


def test_the_transcript_does_not_shrink_to_fit_the_window():
    """`#log` is a flex column, so its children shrink unless told not to.

    This is what three previous scroll fixes missed. A flex item's `flex-shrink`
    defaults to 1, so when the transcript exceeded the viewport the browser did not
    create a scroll range — it compressed every child proportionally. Measured in a
    real browser before the fix: four tool cards at 76px each with
    `scrollHeight == clientHeight`, so there was nothing to scroll to at all. After:
    191px each and `scrollHeight` 985 against a 526px viewport.

    The earlier fixes (N72, N75, N76) were all about *following* a scroll position
    that did not exist.
    """

    css = (WEB / "neuman.css").read_text()
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    assert re.search(r"#log\s*>\s*\*\s*\{[^}]*flex-shrink:\s*0", code), (
        "transcript children have no flex-shrink: 0, so they will compress to fit the "
        "window instead of making the log scroll"
    )


def test_the_live_stage_is_one_fixed_box():
    """The live step's card has one fixed height, and the stage owns its box.

    The card used to grow with its arguments and output and the thinking block
    grew until it hit `min(40vh, 420px)`, so replacing one with the other moved
    the transcript's height by hundreds of pixels and the reader's place with
    it. The live stage is `--step-h`, and both kinds of detail fill it; a
    `max-height` would leave a short card content-sized below the cap, which is
    the jump again (D62). A history row's detail uses the same fixed height in
    its own `.step-detail` box (D64).
    """

    css = (WEB / "neuman.css").read_text()
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    assert re.search(r"--step-h:\s*min\(40vh, 420px\)", code), (
        "there is no single height for a step's card"
    )

    stage = re.search(r"^\s*\.work-run > \.step-stage\s*\{([^}]*)\}", code, re.M)
    assert stage, ".work-run > .step-stage has no rule; this test would pass vacuously"
    block = stage.group(1)
    assert "height: var(--step-h)" in block, "the stage is still sized by its contents"
    assert "max-height" not in block, (
        "the stage is content-sized below its cap, so swapping steps still jumps"
    )
    assert "overflow: hidden" in block, "the stage does not clip its content"
    assert "position: relative" in block, "the stage cannot place its details"

    inline = re.search(r"^\s*\.work-run > \.step-detail\s*\{([^}]*)\}", code, re.M)
    assert inline, "the history disclosure has no box; this test would pass vacuously"
    assert "height: var(--step-h)" in inline.group(1), (
        "a history card is sized by its contents, so opening one jumps differently"
    )

    children = re.search(
        r"\.work-run > \.step-stage > \.step-detail\s*\{([^}]*)\}",
        code,
    )
    assert children, "the stage's details have no rule; this test would pass vacuously"
    block = children.group(1)
    assert "position: absolute" in block, "a detail is in flow, so it can resize the stage"
    assert "height: 100%" in block, "a detail does not fill the stage"
    assert "border: 0" in block and "background: transparent" in block, (
        "a detail paints its own card chrome over the stage"
    )

    # The stage owns the box; the details are content. A height or a border on
    # either base rule would be a second card under the first.
    for selector in (r"\.tool", r"\.reason-detail"):
        match = re.search(rf"^\s*{selector}\s*\{{([^}}]*)\}}", code, re.M)
        assert match, f"{selector} has no rule block; this test would pass vacuously"
        block = match.group(1)
        assert "height: var(--step-h)" not in block, (
            f"{selector} still owns the fixed height instead of filling its box"
        )
        assert "border:" not in block and "background:" not in block, (
            f"{selector} still paints its own card chrome"
        )

    body = re.search(r"\.tool \.tool-body\s*\{([^}]*)\}", code)
    assert body, ".tool .tool-body is gone; this test would pass vacuously"
    assert "min-height: 0" in body.group(1), "the scrolling body cannot shrink"
    assert "overflow: auto" in body.group(1), "the card's body does not scroll"

    thought = re.search(r"\.reasoning-body\s*\{([^}]*)\}", code)
    assert thought, ".reasoning-body is gone; this test would pass vacuously"
    assert "max-height" not in thought.group(1), (
        "the thought is still content-sized, so a long one changes the run's height"
    )
    assert "overflow-y: auto" in thought.group(1), "a long thought cannot be scrolled"


def test_a_card_has_one_scrollport():
    """The card's body scrolls; the output shapes inside it do not.

    Each output box used to carry its own `max-height` and `overflow: auto`,
    which was right while the card grew to hold them. Inside a fixed-height card
    that would nest scrollbars: the outer one only reachable by first scrolling
    the inner one to its end. Nothing is discarded — the body keeps every byte —
    but the scroll areas are one.
    """

    css = (WEB / "neuman.css").read_text()
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    # The one scrollport.
    body = re.search(r"\.tool \.tool-body\s*\{([^}]*)\}", code)
    assert body and "overflow: auto" in body.group(1), "the card's body is not the scrollport"

    # The output shapes inside it. Each was a scrollbox of its own before.
    for selector in (
        r"\.tool pre",
        r"\.tool \.file",
        r"\.tool \.listing",
        r"\.tool \.results",
        r"\.tool \.hits",
        r"\.tool \.read-body",
        r"\.tool \.diff-body",
    ):
        match = re.search(rf"^\s*{selector}\s*\{{([^}}]*)\}}", code, re.M)
        assert match, f"{selector} has no rule; this test would pass vacuously"
        block = match.group(1)
        assert "max-height" not in block, f"{selector} still caps its own height"
        assert "overflow: auto" not in block and "overflow-x: auto" not in block, (
            f"{selector} opens a second scrollbar inside the card"
        )


def test_a_picture_fills_the_card_instead_of_scrolling():
    """A picture is sized to the fixed card, not to a pixel cap.

    The image carried `max-height: 420px` while the body under the head and the
    arguments is smaller than that in every window — `--step-h` is at most 420px
    for the whole card — so a picture at the cap had a scrollbar inside the card
    and part of it was never on screen with the rest. The body now lays out as a
    column, the frame takes the height the arguments and the caption leave, and
    the picture is contained in that frame. The cost is scale: a large picture
    is drawn smaller than its pixels rather than scrolled.

    The frame is what fills; the image is bounded by it. A percentage
    `max-height` on a grid item measured the grid row's content instead, which
    is why the frame is asserted as the flex item with `min-height: 0`.
    """

    css = (WEB / "neuman.css").read_text()
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    shot = re.search(r"\.tool \.shot\s*\{([^}]*)\}", code)
    assert shot, ".tool .shot is gone; this test would pass vacuously"
    assert "max-height: 420px" not in shot.group(1), (
        "the picture has a pixel cap again, so it scrolls inside the fixed card"
    )
    assert "max-height: 100%" in shot.group(1), (
        "the picture is not bounded by the frame that holds it"
    )

    body = re.search(r"\.tool \.tool-body\.shot-body\s*\{([^}]*)\}", code)
    assert body, "the picture card's body has no rule; this test would pass vacuously"
    assert "display: flex" in body.group(1), "the picture body does not lay out its halves"
    assert "flex-direction: column" in body.group(1), (
        "the body is not a column, so the frame cannot take the height left over"
    )

    frame = re.search(r"\.tool \.shot-frame\s*\{([^}]*)\}", code)
    assert frame, "the picture has no frame; this test would pass vacuously"
    assert "flex: 1 1 auto" in frame.group(1), (
        "the frame does not take the height the arguments and caption leave"
    )
    assert "min-height: 0" in frame.group(1), "the frame cannot shrink below its content"

    script = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())
    image = re.search(r"function imageOutput\([^)]*\)\s*\{(.*?)\n\}", script, re.S)
    assert image, "imageOutput is gone; this test would pass vacuously"
    body = image.group(1)
    assert 'closest(".tool-body")' in body, (
        "the card's body never learns the output is a picture"
    )
    assert 'classList.add("shot-body")' in body, (
        "the body is not switched to the picture's column layout"
    )
    assert 'el("div", "shot-frame")' in body, "the picture is not wrapped in its sizing frame"
    assert "frame.appendChild(img)" in body, "the picture left the frame that sizes it"


def test_the_arguments_and_result_share_the_scroll_body():
    """The card's head and buttons stay outside the one scrolling body.

    A fixed-height card only reads as one object if its scrolling half is the
    content: the arguments and the result go inside `.tool-body`, while the
    head and the approve/deny row stay direct children of the card so they do
    not scroll away. The replayed card's deferred arguments are inserted in
    front of the output *inside* that body, which is the same shape the live
    card builds.
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())

    card_fn = re.search(r"function toolCard\(([^)]*)\)\s*\{(.*?)\n\}", code, re.S)
    assert card_fn, "toolCard is gone; this test would pass vacuously"
    body = card_fn.group(2)
    assert "body.appendChild(toolInput(" in body, (
        "the arguments are outside the card's scrolling body"
    )
    assert "body.appendChild(output)" in body, "the result is outside the scrolling body"
    assert "card.appendChild(body)" in body, "the body is not the card's content"
    assert "card.appendChild(ask)" in body, (
        "the approve/deny row is inside the body, where a long diff can hide it"
    )
    assert "body.appendChild(ask)" not in body, "the approve/deny row scrolls with the content"

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", code, re.S)
    assert replay, "replay is gone; this test would pass vacuously"
    assert "output.before(toolInput(" in replay.group(1), (
        "a replayed card inserts its arguments outside the scrolling body"
    )


def test_a_short_conversation_sits_at_the_foot():
    """A short transcript starts beside the composer, not at the top of the page.

    The log is a flex column; with no auto margin its items stack from the top,
    so a new conversation's stage appeared halfway up an empty page rather than
    at the bottom, where the fixed card is meant to be. `margin-top: auto` on the
    first child takes the free space while the content is shorter than the log;
    once it overflows the margin is zero and the scroll range is the content's
    own, so the top is still reachable.
    """

    css = (WEB / "neuman.css").read_text()
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    rule = re.search(r"#log > :first-child\s*\{([^}]*)\}", code)
    assert rule, "the transcript never takes up the free space below a short conversation"
    assert "margin-top: auto" in rule.group(1), (
        "a short conversation sits at the top of the log again"
    )


def test_a_tool_card_does_not_repeat_its_arguments_as_json():
    """The named fields and the code are the arguments; the JSON restated them.

    The card carried a collapsed `raw arguments` disclosure holding the exact JSON,
    which for every tool only said again what the fields already said — a second
    copy of the same facts, opened by nobody. The one thing it showed and the
    fields did not, an array's contents, is either the code (a write's `content`)
    or arrives as a diff at approval, so nothing approval needs is lost.
    """

    script = (WEB / "neuman.js").read_text()
    code = re.sub(r"//.*", "", script)

    assert "argbox" not in code, "the raw-arguments disclosure is back"
    assert "argsum" not in code, "the raw-arguments disclosure is back"

    match = re.search(r"function toolInput\(name, args\)\s*\{(.*?)\n\}", code, re.S)
    assert match, "toolInput is gone; this test would pass vacuously"
    assert "JSON.stringify" not in match.group(1), (
        "the card again carries the exact JSON instead of just the named fields"
    )


def test_a_shell_call_is_drawn_as_a_terminal():
    """A shell call shows its prompt and command before it has any output.

    The command is the call's code, so the argument fields dropped it, and a
    one-line command was not a file either — the card sat blank between its head
    and its result. It now draws the terminal: a prompt naming the account and
    the directory the dispatch starts in, the command (with `>` continuations),
    and a blinking block caret where the output will land. `renderResult`
    removes the caret with the first result, so the wait cannot outlive itself.
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())

    shell = re.search(r"function shellInput\(args\)\s*\{(.*?)\n\}", code, re.S)
    assert shell, "shellInput is gone; this test would pass vacuously"
    body = shell.group(1)
    for needle in (
        '"ps1-user"',
        '"ps1-host"',
        '"ps1-path"',
        '"ps1-mark"',
        '"ps1-cont"',
        '"shell-pending"',
        '"shell-caret"',
    ):
        assert needle in body, f"the shell prompt lost {needle}"
    assert "shortPath(cwd, 40)" in body, "the prompt prints the whole path unbounded"

    tool_input = re.search(r"function toolInput\(name, args\)\s*\{(.*?)\n\}", code, re.S)
    assert tool_input, "toolInput is gone; this test would pass vacuously"
    assert 'name === "shell"' in tool_input.group(1), (
        "a shell call still falls through to the generic argument fields"
    )

    result = re.search(r"function renderResult\(card, result\)\s*\{(.*?)\n\}", code, re.S)
    assert result, "renderResult is gone; this test would pass vacuously"
    assert 'querySelector(".shell-pending")' in result.group(1), (
        "the waiting caret survives its result"
    )
    state = re.search(r"function setToolState\([^)]*\)\s*\{(.*?)\n\}", code, re.S)
    assert state, "setToolState is gone; this test would pass vacuously"
    assert 'outcome === "refused"' in state.group(1), "setToolState no longer hears a refusal"
    assert 'querySelector(".shell-pending")' in state.group(1), (
        "a refused shell keeps blinking for output it will never produce"
    )
    assert "shell: shellOutput" in code, "a shell result goes to the file view again"

    styles = re.sub(r"/\*.*?\*/", "", (WEB / "neuman.css").read_text(), flags=re.S)
    assert ".tool .shell {" in styles, "the terminal has no surface"
    assert ".tool .shell-out {" in styles, "the output is not the terminal's"
    assert "@keyframes shell-blink" in styles, "the caret does not say the shell is waiting"
    assert ".tool .shell-out.failed" in styles, "a failed command loses the terminal surface"


def test_a_step_advances_by_crossfading_the_stage():
    """One card stays; its content fades from one step to the next.

    The old disclosure mounted and unmounted at full height, which moved the
    transcript for 150ms on every step. The stage is a fixed box, so the swap is
    two opacity transitions over the same place: the incoming detail enters
    transparent, the outgoing one leaves, and the old node is removed after the
    fade. The stage's own box never moves, and no step change touches its height.
    """

    script = (WEB / "neuman.js").read_text()
    code = re.sub(r"//.*", "", script)
    css = re.sub(r"/\*.*?\*/", "", (WEB / "neuman.css").read_text(), flags=re.S)

    fade = re.search(
        r"\.work-run > \.step-stage > \.step-detail\s*\{([^}]*)\}",
        css,
    )
    assert fade, "the stage's details have no fade; this test would pass vacuously"
    assert "transition: opacity" in fade.group(1), "a detail swap is not a fade"
    assert "transition: height" not in fade.group(1), (
        "a detail still animates its height, so a step still moves the transcript"
    )
    assert ".work-run > .step-stage > .step-detail.entering { opacity: 0; }" in css
    assert ".work-run > .step-stage > .step-detail.leaving { opacity: 0; }" in css
    assert "const STEP_FADE_MS = 140;" in code, "there is no fade length in the script"
    assert "transition: opacity 140ms" in css, "the stylesheet's fade and the script disagree"

    stage_fn = re.search(r"function stageDetail\(run, row\)\s*\{(.*?)\n\}", code, re.S)
    assert stage_fn, "stageDetail is gone; this test would pass vacuously"
    body = stage_fn.group(1)
    assert 'classList.add("entering")' in body, "the incoming detail never starts transparent"
    assert body.index('classList.add("entering")') < body.index("stage.appendChild(detail)"), (
        "the entering class is added after the detail is visible"
    )
    assert "getBoundingClientRect()" in body, (
        "the start style is never rendered, so the browser skips the fade"
    )
    assert 'classList.remove("entering")' in body, "the incoming detail stays transparent"
    assert 'previous.classList.add("leaving")' in body, "the outgoing detail does not fade"
    assert "stage._detail = detail" in body, "the stage does not remember what it shows"
    assert "STEP_FADE_MS" in body, "the outgoing node is never removed after the fade"
    assert "row.after(" not in body, "the live detail is mounted under its row"


def test_a_fold_lands_in_the_reserved_blank():
    """The live card closes into space the transcript holds, not into a jump.

    A manual close is the reader asking for the space back. The end of a run is
    the other case: prose makes the stage fold, and the reply is written into
    the blank it leaves. Both are height transitions, and the reserve is what
    keeps the content above the card still while the fold runs. Folding at
    `done` was the jump D62 was built to avoid; the reserved blank is what makes
    it safe now (D64).
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())
    css = re.sub(r"/\*.*?\*/", "", (WEB / "neuman.css").read_text(), flags=re.S)

    stage = re.search(r"^\s*\.work-run > \.step-stage\s*\{([^}]*)\}", css, re.M)
    assert stage and "transition: height" in stage.group(1), "the stage cannot fold"
    assert ".work-run > .step-stage.collapsed {" in css, "the stage has no closed state"

    press = re.search(r"function pressRow\(row\)\s*\{(.*?)\n\}", code, re.S)
    assert press, "pressRow is gone; this test would pass vacuously"
    body = press.group(1)
    assert "run._live && run._step === row" in body, (
        "the live row does not know that its card is the stage"
    )
    assert 'classList.add("collapsed")' in body, "the live row cannot close its card"
    assert "motion(" in body, "the manual fold has no frame loop"

    fold = re.search(r"function foldStageForOutput\(run\)\s*\{(.*?)\n\}", code, re.S)
    assert fold, "foldStageForOutput is gone; this test would pass vacuously"
    body = fold.group(1)
    assert "pinBottom()" in body, "the fold does not pin the content above the card"
    assert 'classList.add("collapsed")' in body, "the run does not fold the stage"
    assert "motion(" in body, "the fold has no frame loop"
    assert "stage.remove()" in body, "the stage is never taken out"
    assert "run._stage = null" in body, "the run keeps pointing at a removed stage"
    assert "settle()" in body, "the stage's last pixels are not handed to the reserve"

    # Reduced motion keeps the state change and drops the motion.
    assert re.search(
        r"\.work-run > \.step-stage,\s*"
        r"\.work-run > \.step-stage > \.step-detail,\s*"
        r"\.work-run > \.step-detail \{ transition: none; \}",
        css,
    ), "reduced motion still pays for the fold"
    assert re.search(
        r"\.work-run > \.tool-row\.arrive,\s*"
        r"\.work-run > \.think-row\.arrive \{ animation: none; \}",
        css,
    ), "reduced motion still plays the row arrival"


def test_a_browse_search_renders_as_links_not_a_numbered_file():
    """A list of sites is a list, not a file to be read line by line.

    `browse search` output used to fall through to the generic text view, which set
    it in monospace and numbered every line. It has a real shape — a title, an
    indented URL and a snippet per block — so it gets a renderer: one numbered hit
    per block, the title and URL real links. The other browse actions still fall
    through, and the discriminator is the *indent* on the URL line, which is what
    separates a search block from the header of a `read` or `open` output.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    styles = (src / "neuman.css").read_text()
    code = re.sub(r"//.*", "", script)
    css = re.sub(r"/\*.*?\*/", "", styles, flags=re.S)

    assert "browse: browseOutput" in code, "browse search has no renderer"

    out = re.search(r"function browseOutput\(into, result, body\)\s*\{(.*?)\n\}", code, re.S)
    assert out, "browseOutput is gone; this test would pass vacuously"
    body = out.group(1)
    assert "return false" in body, "the text view is not the fallback"
    assert "externalLink(cls, label, hit.url)" in body, "the hit is not a link"
    link = re.search(r"function externalLink\(([^)]*)\)\s*\{(.*?)\n\}", code, re.S)
    assert link, "externalLink is gone; this test would pass vacuously"
    assert 'a.target = "_blank"' in link.group(2), (
        "a click would navigate the conversation away"
    )
    assert "noopener" in link.group(2), "the opened page can reach back through window.opener"

    hits = re.search(r"function searchHits\(body\)\s*\{(.*?)\n\}", code, re.S)
    assert hits, "searchHits is gone; this test would pass vacuously"
    assert "findIndex" in hits.group(1), "no URL line is located"
    assert r"^\s+https?" in hits.group(1), (
        "the URL check does not require the indent, so a `read` header is a hit"
    )

    assert ".tool .hits" in css, "the hit list has no styling"
    assert ".tool .hit-title" in css and ".tool .hit-url" in css, "the links are unstyled"
    rule = re.search(r"\.tool \.hits\s*\{([^}]*)\}", css)
    assert rule and "monospace" not in rule.group(1), "the hit list is monospace again"


def test_a_browse_read_renders_as_a_page_not_a_numbered_file():
    """A fetched article is a page to read, not a file to read line by line.

    `browse read` prints a `# title` line, the URL, and the extracted text. It was
    hitting the generic view, which numbered every line and set the page in
    monospace. The shape is parsed instead — the `# ` heading is what tells a read
    from the `open` action's bare `title\nurl` — and drawn as a heading, a link and
    the text as paragraphs. Not the site's own HTML: that would want an iframe and a
    sanitiser.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    styles = (src / "neuman.css").read_text()
    code = re.sub(r"//.*", "", script)
    css = re.sub(r"/\*.*?\*/", "", styles, flags=re.S)

    out = re.search(r"function browseOutput\(into, result, body\)\s*\{(.*?)\n\}", code, re.S)
    assert out, "browseOutput is gone; this test would pass vacuously"
    assert "readPage(body)" in out.group(1), "the read shape is not recognized"
    assert "readOutput(into, page)" in out.group(1), "the read page is not drawn"

    page = re.search(r"function readPage\(body\)\s*\{(.*?)\n\}", code, re.S)
    assert page, "readPage is gone; this test would pass vacuously"
    body = page.group(1)
    assert "/^#\\s+/" in body, "the `# title` header is not checked"
    assert "https?" in body, "the URL line is not checked"

    assert ".tool .read-body" in css, "the read body has no styling"
    rule = re.search(r"\.tool \.read-body\s*\{([^}]*)\}", css)
    assert rule and "monospace" not in rule.group(1), "the page is monospace again"
    assert "white-space: pre-wrap" in css, (
        "the extraction's single line breaks collapse, so list items run together"
    )


def test_a_fetched_html_page_renders_as_a_page_not_markup():
    """`web_fetch` returns HTML, so the card should draw the page, not the source.

    The markup was landing in the generic file view: every line numbered, every tag
    monospace, and the article nowhere readable. The response is untrusted (§3,
    D23), so it is parsed into an inert tree and only *text* is read out of it — no
    script runs, no resource loads, and no URL from inside the page becomes a link;
    the only links are the call's own URL. The view is bounded, and the full
    response stays one click away under `raw HTML`, so the bound is a reading, not a
    loss.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    styles = (src / "neuman.css").read_text()
    code = re.sub(r"//.*", "", script)
    css = re.sub(r"/\*.*?\*/", "", styles, flags=re.S)

    assert "web_fetch: webFetchOutput" in code, "web_fetch has no page renderer"

    out = re.search(r"function webFetchOutput\(into, result, body\)\s*\{(.*?)\n\}", code, re.S)
    assert out, "webFetchOutput is gone; this test would pass vacuously"
    body = out.group(1)
    assert 'new DOMParser().parseFromString(body, "text/html")' in body, (
        "the response is not parsed into an inert tree"
    )
    assert "innerHTML" not in body, "untrusted markup would be attached to the live document"
    assert "externalLink" in body, "the call's own URL is not a link"
    assert "PAGE_BLOCKS" in body and "PAGE_CHARS" in body, "the page view is unbounded"
    assert '"raw"' in body and '"pre"' in body, (
        "the full response is discarded rather than disclosed"
    )

    assert ".tool .raw" in css, "the raw disclosure has no styling"
    assert ".tool .read-body .read-h" in css, "headings have no styling"


def test_a_url_shown_as_the_subject_is_not_repeated_in_the_fields():
    """The formatted link on the card head is the URL; the field was the same fact.

    `web_fetch` and `browse` show the URL as the card's subject link, so the `url`
    argument under it repeated the address in monospace — the one line on the card
    that read as machine output under a link saying the same thing. The argument is
    dropped when the card already has that link, and kept for any tool that has a
    URL but no subject link.
    """

    code = re.sub(
        r"//.*",
        "",
        (WEB / "neuman.js").read_text(),
    )

    fields = re.search(r"function argumentFields\(([^)]*)\)\s*\{(.*?)\n\}", code, re.S)
    assert fields, "argumentFields is gone; this test would pass vacuously"
    assert 'if (toolHref(name, args)) skip.add("url")' in fields.group(2), (
        "a URL already shown as the card's subject is repeated in the fields"
    )


def test_a_scratch_read_is_titled_with_the_call_that_wrote_it():
    """A scratch filename is a random dispatch id, so the row must name the call.

    The client writes `<dispatch_id>.stdout.txt`, and the dispatch id is minted per
    dispatch rather than being the tool-call id — so the name says nothing about
    what the model is reading. The result that wrote the file is keyed to the call
    and now carries the path as `persisted` (D47); `rememberScratch` records it and
    `fs_read`/`fs_search` consult it, so the row reads `stdout of Run “pytest -q”`
    instead of `6fddbbd2….stdout.txt`.
    """

    code = re.sub(
        r"//.*",
        "",
        (WEB / "neuman.js").read_text(),
    )

    assert "let scratchOrigins = new Map();" in code, "there is nowhere to record an origin"
    assert "rememberScratch(result, card)" in code, (
        "a persisted result is not recorded against the call that wrote it"
    )
    assert code.count("scratchOrigin(a.path)") >= 2, (
        "fs_read and fs_search do not consult the recorded origin"
    )
    # Per view rather than per window (D63): the map is swapped with the view
    # being drawn, so a scratch file read in one session cannot be titled with
    # the call from another. There is nothing to clear on a switch now — the
    # map belongs to the view, and dropping the view drops it.
    assert "scratchOrigins: new Map()" in code, "a view does not own its scratch map"
    assert "scratchOrigins = view.scratchOrigins;" in code, (
        "a background frame reads the window's scratch map"
    )
    assert "view.scratchOrigins = scratchOrigins;" in code, (
        "leaving a view does not keep its scratch map with it"
    )


def test_a_live_row_stages_and_a_history_row_opens_in_place():
    """The live step's card is at the run's foot; history opens under its row.

    The stage is what keeps a step change still: the newest row goes in front of
    the card and the card crossfades in place (D62). But a session's older rows
    are not a singleton: a press on one mounts its detail directly after that
    row, so a reader scrolled up can open it where they are looking instead of
    replacing the live card (D64). A detail is in one home only — `_mounted` is
    "stage", "inline" or null.
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())

    stage = re.search(r"function stageFor\(run\)\s*\{(.*?)\n\}", code, re.S)
    assert stage, "stageFor is gone; this test would pass vacuously"
    assert '"step-stage collapsed"' in stage.group(1), "the stage has no class to style"
    assert "run._stage = stage" in stage.group(1), "the run cannot find its stage again"
    assert "run.appendChild(stage)" in stage.group(1), "the stage is not the run's foot"

    staging = re.search(r"function stageDetail\(run, row\)\s*\{(.*?)\n\}", code, re.S)
    assert staging, "stageDetail is gone; this test would pass vacuously"
    body = staging.group(1)
    assert "stage.appendChild(detail)" in body, "the detail never reaches the stage"
    assert "row.after(" not in body, "the live detail is mounted under its row"
    assert "stage._row = row" in body, "the stage does not remember which row it shows"
    assert 'detail._mounted = "stage"' in body, "the detail does not record its home"

    inline = re.search(r"function openInline\(row\)\s*\{(.*?)\n\}", code, re.S)
    assert inline, "openInline is gone; this test would pass vacuously"
    body = inline.group(1)
    assert "row.after(detail)" in body, "a history detail is not mounted under its row"
    assert 'detail._mounted = "inline"' in body, "the detail does not record its home"

    press = re.search(r"function pressRow\(row\)\s*\{(.*?)\n\}", code, re.S)
    assert press, "pressRow is gone; this test would pass vacuously"
    body = press.group(1)
    assert "run._live && run._step === row" in body, "the live row does not toggle the stage"
    assert "openInline(row)" in body, "a history row does not open under itself"
    assert "closeInline(row)" in body, "a history row cannot close its own card"

    for name in ("toolCard", "reasoningBlock"):
        fn = re.search(rf"function {name}\([^)]*\)\s*\{{(.*?)\n\}}", code, re.S)
        assert fn, f"{name} is gone; this test would pass vacuously"
        body = fn.group(1)
        assert "appendWork(row, { animate })" in body, f"{name} never appends its row"
        assert "row._detail" in body, f"{name} never gives its row a detail"
        assert "pressRow(row)" in body, f"{name} does not toggle on press"


def test_the_work_run_stages_its_newest_step():
    """The chain advances: the newest step's detail crossfades onto the stage.

    Every card staying open made a turn with three calls a wall of expanded cards
    with the reply pushed off the window. `followStep` puts the newest step on the
    run's one stage; the reasoning frame and the tool proposal both go through it,
    so a thought between two calls and the calls themselves advance the same way
    (D44). The stage is height-neutral, so the swap is a crossfade, not a fold.
    Prose is what ends the run: `add` folds the stage into the reserved blank and
    the reply fills it; a run that ends without prose leaves its stage as the
    artifact.
    """

    src = WEB
    code = re.sub(r"//.*", "", (src / "neuman.js").read_text())
    styles = (src / "neuman.css").read_text()

    follow = re.search(r"function followStep\(row[^)]*\)\s*\{(.*?)\n\}", code, re.S)
    assert follow, "followStep is gone; this test would pass vacuously"
    body = follow.group(1)
    assert "stageDetail(run, row)" in body, "the step is not staged"
    assert "run._step = row" in body, "the run does not track its live step"
    assert "run._live = true" in body, "the run is not marked live"
    assert "closeInline(" not in body, "advancing a step closes an inline card"

    handler = _handle(code)
    reasoning = re.search(r'case "reasoning":(.*?)break;', handler, re.S).group(1)
    assert "grow(() => followStep(reasoningTurn))" in reasoning, (
        "a new thinking block stages outside grow(), so creating the stage does not follow"
    )
    proposal = re.search(r'case "tool_proposal":(.*?)break;', handler, re.S).group(1)
    assert "grow(() => followStep(toolRows.get(frame.tool_call_id)))" in proposal, (
        "a tool card stages outside grow(), so creating the stage does not follow"
    )

    add = re.search(r"function add\(node[^)]*\)\s*\{(.*?)\n\}", code, re.S)
    assert add, "add is gone; this test would pass vacuously"
    assert "foldStageForOutput(run)" in add.group(1), "prose does not end the run's stage"

    done = handler.split('case "done":', 1)[1].split("break;", 1)[0]
    assert "releaseReserve(" in done, "a reply shorter than the blank leaves it open"
    assert "endThought()" in done and "workRun = null" in done, (
        "a finished turn does not end the run"
    )

    # The thinking body is bounded by the stage's fixed height and scrolls inside
    # it: an unbounded one pushed the reply off the window as it streamed.
    rule = re.search(r"\.reasoning-body\s*\{([^}]*)\}", styles)
    assert rule, ".reasoning-body is gone; this test would pass vacuously"
    assert "max-height" not in rule.group(1), "the thought is sized by its contents again"
    assert "overflow-y: auto" in rule.group(1), "the bounded thought cannot be scrolled"
    stage = re.search(r"^\s*\.work-run > \.step-stage\s*\{([^}]*)\}", styles, re.M)
    assert stage and "height: var(--step-h)" in stage.group(1), (
        "the run's stage is not the one fixed box"
    )
    detail = re.search(r"^\s*\.reason-detail\s*\{([^}]*)\}", styles, re.M)
    assert detail and "height: var(--step-h)" not in detail.group(1), (
        "the thinking detail owns the height again instead of filling its box"
    )


def test_the_waiting_state_is_not_a_transcript_event():
    """The model's silence lives on the rail, not in the log.

    `waiting` is advisory: the server emits it after two seconds of model
    silence, and on a heavy prefill that happens on most calls. It used to be a
    line in the transcript that arrived and then went, so the page moved twice
    per model call. It is a state, and the transcript is a record of events;
    `trackViewFrame` already puts "waiting for the model" on the rail, and the
    composer's Stop button carries the clock. No line, no removal, no seam.
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())

    waiting = re.search(r'case "waiting":(.*?)break;', _handle(code), re.S)
    assert waiting, "the waiting frame is gone; this test would pass vacuously"
    body = waiting.group(1)
    assert "add(" not in body, "the waiting state is still drawn as an event"
    assert "showWaiting(" not in body, "the waiting note is back"
    assert "showWaiting" not in code and "clearWaiting" not in code, (
        "the waiting note's machinery is still in the script"
    )

    track = re.search(r"function trackViewFrame\(view, frame\)\s*\{(.*?)\n\}", code, re.S)
    assert track, "trackViewFrame is gone; this test would pass vacuously"
    assert "waiting for the model" in track.group(1), "the rail does not carry the wait"

    css = re.sub(r"/\*.*?\*/", "", (WEB / "neuman.css").read_text(), flags=re.S)
    assert ".turn.note.waiting" not in css, "the waiting line still has a style"


def test_the_rail_is_centred_on_its_nodes():
    """The dots sit on the spine, not beside it.

    The rail is a 2px bar placed in the run; a node is an 8px circle placed
    against its row, and a row starts at the run's 16px padding. With the bar at
    `left: 4px` its centre was 5px into the run while every node centred on 4px,
    so each dot sat a pixel to the left of the line — the spine met the circle
    off-centre at both ends (N151).
    """

    css = re.sub(r"/\*.*?\*/", "", (WEB / "neuman.css").read_text(), flags=re.S)

    run = re.search(r"\.work-run\s*\{([^}]*)\}", css)
    rail = re.search(r"\.work-run::before\s*\{([^}]*)\}", css)
    node = re.search(
        r"\.work-run > \.tool-row::before,\s*\.work-run > \.think-row::before\s*\{([^}]*)\}",
        css,
    )
    assert run and rail and node, (
        "the rail or its nodes are gone; this test would pass vacuously"
    )

    def px(block, prop):
        match = re.search(rf"(?:^|[;\s]){prop}:\s*(-?\d+(?:\.\d+)?)px", block)
        assert match, f"no {prop} in {block!r}"
        return float(match.group(1))

    inset = px(run.group(1), "padding-left")
    node_centre = inset + px(node.group(1), "left") + px(node.group(1), "width") / 2
    rail_centre = px(rail.group(1), "left") + px(rail.group(1), "width") / 2
    assert rail_centre == node_centre, (
        f"the rail centres on {rail_centre}px and its nodes on {node_centre}px"
    )


def test_a_reopened_session_starts_collapsed():
    """Opening a past session expands nothing.

    `replay` builds each row and its detail but never mounts one, so every row is
    there to click and none is expanded. A reader who asked to review a session
    has not asked to look at its last step — which the old auto-show did, leaving
    the last thinking block or tool card open the moment the session landed.
    """

    code = re.sub(
        r"//.*",
        "",
        (WEB / "neuman.js").read_text(),
    )

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", code, re.S)
    assert replay, "replay is gone; this test would pass vacuously"
    assert "openInline(" not in replay.group(1), (
        "a replay opens a row, so a session lands with its last step expanded"
    )


def test_a_reopened_card_builds_its_detail_on_first_open():
    """Opening a session builds the rows, not every card's body.

    A long transcript is mostly tool calls and thinking the reader is not looking
    at. `replay` therefore defers the arguments, the result and a write's diff to
    the first open of that row, and both mounts run the deferred builder before
    the detail enters the tree, so the card opens with its body already in it
    rather than filling in while it is visible.
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())

    reveal = re.search(r"function revealDetail\(detail\)\s*\{(.*?)\n\}", code, re.S)
    assert reveal, "revealDetail is gone; this test would pass vacuously"
    assert "_reveal" in reveal.group(1), "the deferred body is never run"

    inline = re.search(r"function openInline\(row\)\s*\{(.*?)\n\}", code, re.S)
    assert inline, "openInline is gone; this test would pass vacuously"
    body = inline.group(1)
    assert "revealDetail(detail)" in body, (
        "a deferred card is mounted without its body, so the detail opens empty"
    )
    assert body.index("revealDetail(detail)") < body.index("row.after(detail)"), (
        "the deferred body is built after the card is mounted, so it fills in while visible"
    )

    staging = re.search(r"function stageDetail\(run, row\)\s*\{(.*?)\n\}", code, re.S)
    assert staging, "stageDetail is gone; this test would pass vacuously"
    body = staging.group(1)
    assert "revealDetail(detail)" in body, (
        "a replayed card staged on the live path is mounted empty"
    )
    assert body.index("revealDetail(detail)") < body.index("stage.appendChild(detail)"), (
        "the deferred body is built after the card is staged, so it fills in while visible"
    )

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", code, re.S)
    assert replay, "replay is gone; this test would pass vacuously"
    body = replay.group(1)
    assert "deferred: true" in body, (
        "a replayed tool card still builds its arguments and controls up front"
    )
    assert "animate: false" in body, "a replayed row still plays the live arrival animation"
    assert "row._detail._reveal" in body, "a replayed thought is never filled"
    assert "card._reveal =" in body, "a replayed tool card has no deferred detail"


def test_a_tool_call_closes_the_open_bubbles():
    """Reasoning after a tool call must appear *below* the card, not above it.

    `reasoningTurn` and `agentTurn` were nulled only at `done`, so post-tool text
    appended to the bubble created before the tool. A turn with three calls read as one
    wall of thinking at the top followed by three cards, instead of
    reason → call → reason → call, which is the order it happened in.
    """

    script = (WEB / "neuman.js").read_text()
    code = re.sub(r"//.*", "", script)

    match = re.search(r'case "tool_proposal":(.*?)break;', _handle(code), re.S)
    assert match, "the tool_proposal handler is gone; this test would pass vacuously"
    body = match.group(1)

    assert "agentTurn = null" in body, "a tool call leaves the assistant bubble open"
    assert "endThought()" in body, "a tool call leaves the reasoning bubble open"


def test_a_turn_without_prose_leaves_its_card():
    """A run that ends before prose leaves its stage as the turn's artifact.

    Prose is what folds the stage (D64): it is the moment there is an answer to
    write into the blank. A turn that stops after a tool call or a thought has
    no answer, so the card stays where it is, and `done` neither folds nor
    removes it. `releaseReserve` is called there for the other case — a blank
    the reply did not fill — and is a no-op when there is none.
    """

    script = (WEB / "neuman.js").read_text()
    code = re.sub(r"//.*", "", script)

    handler = _handle(code).split('case "done":', 1)[1].split("break;", 1)[0]
    assert "foldStageForOutput(" not in handler, "done folds a card with no answer to fill it"
    assert "stage.remove()" not in handler, "done removes the turn's artifact"
    assert "releaseReserve(" in handler, "a reply shorter than the blank leaves it open"
    assert "toolRows" not in handler, "a tool's staged card is folded away"
    assert "endThought()" in handler and "workRun = null" in handler, (
        "a finished turn does not end its run"
    )


def test_the_rail_is_an_overlay_at_every_width():
    """The session list floats over the transcript rather than displacing it.

    As a grid column it squeezed the transcript when it opened and squeezed it
    harder when dragged wider — the opposite of what a list you glance at should
    do. It is `position: fixed` with a shadow at every width now; the transcript
    and the composer keep their places. Folded is `display: none`, because an
    overlay has no track to keep open and a rail hidden behind the transcript must
    not stay in the tab order.
    """

    styles = (WEB / "neuman.css").read_text()

    assert "grid-template-columns: minmax(0, 1fr);" in styles
    assert not re.search(r'grid-template-areas:[^;]*"rail', styles, re.S), (
        "the rail is a grid column again"
    )

    match = re.search(r"#sessions\s*\{([^}]*)\}", styles)
    assert match, "#sessions has no rule; this test would pass vacuously"
    block = match.group(1)
    assert "position: fixed" in block, "the rail is not an overlay"
    assert "var(--sessions-width)" in block
    assert "body.rail-collapsed #sessions { display: none; }" in styles


def test_there_is_no_inspector_pane():
    """The step detail is inline; the second pane is gone, not hidden.

    A tool card and a chain of thought are the row's own disclosure now, so the
    pane, its grip, its `live` control and the breakpoint that chose between the
    two mounts have no reason to exist. This pins the removal so a partial
    resurrection — a leftover `#inspector` rule, a `$("inspector-live")` lookup —
    fails here rather than at runtime.
    """

    src = WEB
    markup = (src / "index.html").read_text()
    script = (src / "neuman.js").read_text()
    styles = (src / "neuman.css").read_text()

    assert 'id="inspector"' not in markup
    assert 'id="inspector-grip"' not in markup
    assert "inspector" not in script, "the client still names the inspector"
    assert "inspector" not in styles, "the stylesheet still styles the inspector"
    assert "--inspector" not in styles


def test_the_rail_has_a_grip_that_drags_and_folds():
    """The rail's grip is fixed outside the overlay, and does both jobs.

    A grip inside the pane would fold away with it and leave nothing to click, so
    it sits outside and reads the rail's width variable (N121). It is also the
    drag handle, which is why it is a button rather than a plain toggle.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()
    styles = (src / "neuman.css").read_text()

    assert 'id="sessions-grip"' in markup
    assert 'aria-controls="sessions"' in markup
    assert '$("sessions-grip")' in script
    assert "function setRailCollapsed(" in script
    # The grip is a button, so it must fold on a keyboard activation too; the
    # pointer path cannot see Enter, and a `click` from a pointer would fold twice.
    assert "event.detail === 0" in script, "the grip cannot be folded by keyboard"
    assert "body.rail-collapsed #sessions-grip" in styles
    assert "rotate(180deg)" in styles
    # The width and the grip share one variable (N121).
    assert "left: calc(var(--sessions-width) - 14px);" in styles

    # The grip straddles the rail's edge, so it has to paint *above* the rail.
    # It did not: `#sessions` was 30 and the grip 25, so the panel covered the
    # half of the circle that overlaps it and an open rail showed a semicircle
    # that read as a clipped button. Invisible while collapsed, because the
    # panel is `display: none` then and there was nothing to paint over it.
    #
    # Asserted as an *ordering* rather than as literal numbers, so renumbering
    # the layers stays free and only the relationship is pinned.

    def layer(selector):
        match = re.search(rf"^\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", styles, re.M)
        assert match, f"{selector} has no rule; this test would pass vacuously"
        found = re.search(r"z-index:\s*(-?\d+)", match.group(1))
        assert found, f"{selector} has no z-index"
        return int(found.group(1))

    assert layer("#sessions-grip") > layer("#sessions"), (
        "the rail paints over its own grip, so the handle looks cut off"
    )
    # And still under the row menu, which floats over everything in the rail.
    assert layer(".row-menu") > layer("#sessions-grip")


def test_the_rail_edge_is_a_second_drag_handle():
    """The whole right edge resizes; the grip keeps the fold.

    The grip is a 28px circle in the vertical middle, which reads as a button —
    "drag the right side" is what a hand reaches for instead. The edge handle is
    full height, pointer-only, and has no fold, so a press that does not move
    does nothing; the grip is the keyboard control and the only thing that folds.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()
    styles = (src / "neuman.css").read_text()

    assert 'id="sessions-resize"' in markup
    assert '$("sessions-resize")' in script
    assert "body.rail-collapsed #sessions-resize" in styles, (
        "the edge handle is still grabbable with the rail folded away"
    )
    # One drag for both handles, and the fold on the grip's press alone.
    assert "startRailDrag(event, true)" in script, "the grip no longer folds"
    assert "startRailDrag(event, false)" in script, "the edge handle folds or is gone"
    # The click-away loop exempts it by identity, like the grip: a press on the
    # edge is a press inside the rail, or it would fold the rail it is resizing.
    assert '$("sessions-resize").contains(event.target)' in script

    def layer(selector):
        match = re.search(rf"^\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", styles, re.M)
        assert match, f"{selector} has no rule; this test would pass vacuously"
        found = re.search(r"z-index:\s*(-?\d+)", match.group(1))
        assert found, f"{selector} has no z-index"
        return int(found.group(1))

    # The grip paints over the edge so its click still lands, and the edge over
    # the rail so the half that straddles the border is grabbable.
    assert layer("#sessions-grip") > layer("#sessions-resize")
    assert layer("#sessions-resize") > layer("#sessions")


def test_the_rail_width_is_a_constant_with_the_window_as_a_ceiling():
    """The rail is a reading measure, so its width is fixed; the window caps it.

    It was `16rem` and nothing else, then `clamp(220px, 22vw, 380px)` for one
    commit: a width read from the *viewport* standing in for a reading measure, so
    the same rail was a different size in two browsers whose windows were not the
    same size, and narrower than the constant it replaced on any window under
    1164px (N136). A fixed length inside `min()` keeps the readable width where
    there is room and lets the window overrule it only where there is not.

    This pins the *shape*, not the pixels. The numbers are a browser measurement,
    and a static read of the file cannot make one — asserting them here would be a
    test that passes for the wrong reason if the expression ever changed meaning.
    """

    src = WEB
    styles = (src / "neuman.css").read_text()
    script = (src / "neuman.js").read_text()

    token = re.search(r"--sessions-width:\s*([^;]+);", styles)
    assert token, "--sessions-width is gone; this test would pass vacuously"
    value = token.group(1).strip()

    body, _, ceiling = value.partition(",")
    assert body.startswith("min("), f"the rail's width is not a constant: {value}"
    assert "vw" not in body, f"the rail's width scales with the window: {value}"
    assert "100vw" in ceiling, f"the width has no ceiling on the window: {value}"

    # The strip the narrow branch leaves is one number, named by the rail's
    # ceiling and by the width the drag handler restores, so the two cannot come
    # apart (N121).
    assert styles.count("--rail-strip:") == 1, "the strip is declared more than once"
    assert "var(--rail-strip)" in script, "the client does not name the strip"
    assert not re.search(r"100vw\s*-\s*\d+px", script), (
        "the client carries its own copy of the strip"
    )

    # A layout width declared among the palette's colours is a width one palette
    # rewrite away from disappearing. It was written there, and moved out.
    palette_css = (src / "palette.css").read_text()
    palette = re.search(
        r':root,\s*\.swatch\[data-palette="dark"\]\s*\{(.*?)\n  \}', palette_css, re.S
    )
    assert palette, "the dark palette block is not where this test expects"
    assert "--sessions-width" not in palette.group(1), (
        "the rail's width is a palette token again"
    )

    # One writer for the width, and both callers go through it: the saved value
    # read at boot, and the drag. A second place formatting a bare `${px}px` is
    # how the window ceiling gets lost on one path only.
    assert "function railWidthValue(" in script
    writes = re.findall(r'setProperty\(\s*"--sessions-width",\s*railWidthValue\(', script)
    assert len(writes) == 2, f"the width is written {len(writes)} times through one writer"

    # The stored width is read under a key the buggy handler never wrote. A
    # per-browser preference outlives the code that produced it, so a value the
    # old drag ratcheted down would otherwise keep the bug alive in exactly the
    # browser that had been dragged (N136).
    assert 'storage: "neuman.sessions.width.v2"' in script, (
        "the client reads the stored width under the key the old drag wrote"
    )

    # The variable holds a `min()` now, so re-parsing it would store `NaN`.
    assert "parseInt(current" not in script, "the drag re-parses the width variable"


def test_the_model_control_moved_to_the_status_line():
    """The model and GPU chip is a per-turn choice, not header furniture.

    It sits beside approval and reasoning — reasoning levels are the model's own
    declaration, so the two belong adjacent — and the header band that used to
    carry it is gone. Asserted against the markup nesting because that nesting is
    what makes them one row.
    """

    src = WEB
    markup = (src / "index.html").read_text()
    script = (src / "neuman.js").read_text()
    styles = (src / "neuman.css").read_text()

    status = re.search(r'<div id="status">(.*?)\n  </div>', markup, re.S)
    assert status, "the status line is not where this test expects"
    body = status.group(1)
    for control in (
        'id="gpu-toggle"',
        'id="model"',
        'id="reasoning-chip"',
        'id="approval-chip"',
    ):
        assert control in body, f"{control} is not on the status line"

    assert "<header" not in markup, "the header bar is back"
    assert 'id="sessions-toggle"' not in markup, "the header's rail toggle is back"
    assert "--bar-height" not in styles, "a bar height is still being measured"
    assert "--bar-height" not in script, "a bar height is still being measured"


def test_the_sessions_are_grouped_and_filterable():
    """A fifty-row wall becomes a few dated lists, with a type-ahead over them.

    The filter is the server's (`?q=`, D52): the listing is capped and the
    conversation is what is searchable, so the browser neither narrows these
    rows nor holds the transcript to match against.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()

    assert 'id="sessions-filter"' in markup, "there is no type-ahead field"
    assert 'class="chooser-filter"' in markup, "the field does not reuse the chooser"
    for name in (
        "function sessionGroup(",
        "function renderSessions(",
        "function sessionRow(",
    ):
        assert name in script, f"{name} is gone"
    assert "groupHead(label)" in script, "the rows are not grouped by date"


def test_a_pinned_session_is_its_own_section():
    """A pin is the reader's, kept on the server, and drawn above the dates.

    The list is capped at fifty, so the ordering is a query rather than a sort
    here — a pin applied in the browser would top whichever fifty the server
    happened to return. The section exists because a date heading cannot say
    "first because you pinned it": a pinned row inside the date groups would
    either split a day in two or sit under a heading that contradicts why it is
    at the top.
    """

    script = (WEB / "neuman.js").read_text()

    render = re.search(r"function renderSessions\(.*?\n\}", script, re.S)
    assert render, "renderSessions is gone; this test would pass vacuously"
    body = render.group(0)
    assert 'groupHead("Pinned")' in body, "pinned rows have no section"
    assert "session.pinned_at" in body, "the split between pinned and dated rows is gone"
    assert "activeIds.has(session.id)" in body, (
        "a running session is drawn twice, once in Active and once in its date"
    )


def test_the_active_section_is_the_working_set_above_pinned():
    """The sessions this window has open come before the arrangement of history.

    Active is not a date and not a pin: it is the set the reader is currently
    working in — on screen, running a turn, or holding a reply they have not
    looked at. Drawn by the same renderer so it cannot drift from the rows
    below it, and drawn first because a pinned session is still something you
    have to go and open.
    """

    script = (WEB / "neuman.js").read_text()

    render = re.search(r"function renderSessions\(.*?\n\}", script, re.S)
    assert render, "renderSessions is gone; this test would pass vacuously"
    body = render.group(0)

    assert 'groupHead("Active"' in body, "the working set has no section"
    assert 'groupHead("Pinned")' in body, "the pinned section is gone"
    assert body.index('groupHead("Active"') < body.index('groupHead("Pinned")'), (
        "Active is not above Pinned"
    )
    assert "activeGroups()" in body, "the section is not built from the open views"
    assert "activeRow(group)" in body, "an active session is not drawn as one"


def test_a_switch_moves_the_transcript_and_keeps_its_socket():
    """A row click must not be a Stop.

    `ws.py` cancels a turn in the socket's `finally`, so a client with one
    global socket makes every switch cancel the turn. The view owns the socket
    and the nodes; a hidden view draws into a detached holder, and a switch
    moves those nodes into `#log` instead of rebuilding them — a replay here is
    the flash of a finished reply this design exists to avoid.
    """

    script = (WEB / "neuman.js").read_text()

    for name in (
        "const views = new Map();",
        "function makeView(",
        "function showView(",
        "function stashViewDom(",
        "function attachView(",
        "function withView(",
    ):
        assert name in script, f"{name} is gone"

    connect = re.search(r"function connect\(([^)]*)\)\s*\{(.*?)\n\}", script, re.S)
    assert connect, "connect() is gone; this test would pass vacuously"
    assert connect.group(1).strip() == "view", (
        "the socket is still opened for one global session"
    )
    assert "ws/branches/${view.id}" in connect.group(2), (
        "the connection is not named for its view"
    )
    assert "withView(view, () => handle(frame))" in connect.group(2), (
        "a background frame is not drawn into its own view"
    )

    send = re.search(r"function send\(([^)]*)\)\s*\{(.*?)\n\}", script, re.S)
    assert send, "send() is gone; this test would pass vacuously"
    assert "view = currentView" in send.group(1), (
        "send() cannot be told which session the message belongs to"
    )
    assert "view.socket" in send.group(2), (
        "a send goes to whichever socket is current, not the session it was typed in"
    )

    show = re.search(r"function showView\(.*?\n\}", script, re.S)
    assert show, "showView is gone; this test would pass vacuously"
    assert "replay(" not in show.group(0), "a switch rebuilds the transcript"
    assert "attachView(view)" in show.group(0), "a switch does not mount the view's nodes"


def test_a_background_frame_draws_into_its_own_view():
    """The renderers keep their single-session shape; the view supplies the names.

    A `token` frame for a hidden session has to append to that session's
    transcript, not to the one on screen. `loadRender` points `log` at the
    view's holder; the chrome writers are gated by `isLiveRender` so a
    background frame cannot touch this composer's usage, busy state or notes.
    """

    script = (WEB / "neuman.js").read_text()

    load = re.search(r"function loadRender\(view\).*?\n\}", script, re.S)
    assert load, "loadRender is gone; this test would pass vacuously"
    assert 'view === currentView ? $("log") : view.holder' in load.group(0), (
        "a hidden view is not drawn into its holder"
    )

    track = re.search(r"function trackViewFrame\(.*?\n\}", script, re.S)
    assert track, "trackViewFrame is gone; this test would pass vacuously"
    body = track.group(0)
    assert "view.unread = view !== currentView" in body, (
        "a background reply is not marked unread"
    )
    assert "if (view.unread) view.followTail = true" in body, (
        "an unread reply does not open at the reply"
    )
    assert "closeViewSocket(view)" in body, (
        "a finished background turn keeps a socket it no longer needs"
    )

    connect = re.search(r"function connect\(([^)]*)\)\s*\{(.*?)\n\}", script, re.S)
    assert connect, "connect() is gone; this test would pass vacuously"
    assert "trackViewFrame(view, frame)" in connect.group(2), (
        "the rail state is not folded in before the frame is drawn"
    )


def test_the_frame_renderer_names_the_view_it_is_drawing():
    """`handle` has no view parameter, so a bare `view` is a `ReferenceError`.

    The branch change added turn bookkeeping to `user_message` and `done` and
    wrote it against `view` — the parameter `trackViewFrame` takes. `handle` is
    the transcript renderer and addresses the render context instead (D63), so
    every user message threw `view is not defined` before its bubble was drawn.
    N99's catch turned that into a note in the transcript, which is how the
    missing message was reported rather than the console staying silent.

    The fix is the same shape as the rest of the file, not a second parameter:
    `withView` swaps `renderView` with the frame's branch, and every renderer
    reads the name it was given.
    """

    script = (WEB / "neuman.js").read_text()
    body = _handle(script)

    assert "const view = renderView;" in body, (
        "handle() reaches for a view it was never given; a bare `view` throws"
    )
    assert "currentView" not in body, (
        "the transcript renderer addresses the view on screen, not the frame's own"
    )


def test_a_replayed_transcript_seeds_the_turn_count_it_counted():
    """The next turn typed into a reopened session is not the first turn.

    `replay` counts turns to place its own edit actions, then threw the
    count away; the view was left at zero, so the opening message of the next
    turn looked like turn one and the action was missing from a conversation with
    history. The count belongs to the view, which is what the live path reads.
    """

    script = (WEB / "neuman.js").read_text()

    body = _handle(script)
    assert "const opensTurn = !view.turnOpen;" in body, (
        "the live path no longer reads the view's turn state"
    )

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", script, re.S)
    assert replay, "replay() is gone; this test would pass vacuously"
    assert "renderView.turnIndex = turnIndex;" in replay.group(1), (
        "a reopened transcript leaves the view's turn count at zero"
    )

    view = re.search(r"function makeView\(session, branch\)\s*\{(.*?)\n\}", script, re.S)
    assert view, "makeView is gone; this test would pass vacuously"
    assert "turnOpen: false" in view.group(1), (
        "a view starts without the turn state its first user message reads"
    )


def test_a_turn_that_ends_without_done_is_not_still_open():
    """A dropped socket and a send that never left both end a turn with no `done`.

    `turnOpen` is what tells the next user message whether it opens a turn or
    steers one. Left set by a turn whose socket died, that message is drawn as a
    steering row — no turn count, no edit action, and a fork's first-message
    label never written. The flag is cleared wherever a turn stops being live.
    """

    script = (WEB / "neuman.js").read_text()

    failed = re.search(r"function markTurnFailed\(.*?\n\}", script, re.S)
    assert failed, "markTurnFailed() is gone; this test would pass vacuously"
    assert "view.turnOpen = false;" in failed.group(0), (
        "a send that never left leaves the view claiming a turn is open"
    )

    connect = re.search(r"function connect\(([^)]*)\)\s*\{(.*?)\n\}", script, re.S)
    assert connect, "connect() is gone; this test would pass vacuously"
    assert "view.turnOpen = false;" in connect.group(2).split("sock.onclose", 1)[1], (
        "a dropped socket leaves the view steering a turn the server cancelled"
    )


def test_editing_a_prompt_forks_before_it_and_sends_the_edit():
    """The edit is the fork gesture, and the source branch is never rewritten.

    The bubble itself is the editor; Send forks *before* the turn and sends the
    edited text into the new branch. The source branch, and everything below the
    fork, stays.
    """

    script = (WEB / "neuman.js").read_text()

    start = re.search(r"function startEdit\(.*?\n\}", script, re.S)
    assert start, "startEdit() is gone; this test would pass vacuously"
    assert "node.replaceChildren(editorEl(view))" in start.group(0), (
        "the prompt bubble is not the editor"
    )

    fork = re.search(r"async function forkAtTurn\(.*?\n\}", script, re.S)
    assert fork, "forkAtTurn() is gone; this test would pass vacuously"
    assert 'side: "before"' in fork.group(0), (
        "an edit forks after the turn instead of before it"
    )
    assert "at_seq: atSeq" in fork.group(0)

    commit = re.search(r"async function commitEdit\(.*?\n\}", script, re.S)
    assert commit, "commitEdit() is gone; this test would pass vacuously"
    assert "sendEdited(opened, text)" in commit.group(0), (
        "the edited text is not sent into the branch it created"
    )


def test_an_edit_action_is_only_on_a_prompt_this_branch_wrote():
    """The inherited prefix belongs to another branch's history.

    A branch's copied rows stop at `inherited_event_count`; its own first event
    is the next row. Offering an edit on an inherited bubble would fork inside a
    prefix this branch does not own, and the turn it named would belong to
    somebody else.
    """

    script = (WEB / "neuman.js").read_text()
    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", script, re.S)
    assert replay, "replay() is gone; this test would pass vacuously"
    body = replay.group(1)
    assert "const opensTurn = message.opens_turn === true;" in body, (
        "a steering row is counted as a new turn"
    )
    assert "const ownStart = (renderView.branch.inherited_event_count || 0) + 1;" in body
    assert "message.seq >= ownStart" in body, (
        "an inherited prompt can be edited as though this branch wrote it"
    )
    assert "turnIndex > 1" in body, "the first turn has nothing before it to fork"
    assert "attachEditAction(node" in body, "the pencil is not on the user bubble"

    live = _handle(script)
    assert "const ownStart = (view.branch.inherited_event_count || 0) + 1;" in live
    assert "frame.seq >= ownStart" in live, (
        "a live inherited prompt can be edited as though this branch wrote it"
    )


def test_a_fork_point_is_owned_by_the_branch_whose_turn_ended_there():
    """A fork node is a boundary, not a branch.

    Two branches made at the same completed turn must draw one node, under the
    branch that owns that turn. `inherited_event_count` includes the fork marker,
    so a branch's own events begin one row after it; the next chain child's
    inherited count is the last row the parent still owns.
    """

    script = (WEB / "neuman.js").read_text()
    owner = re.search(r"function ownerAtBoundary\(.*?\n\}", script, re.S)
    assert owner, "ownerAtBoundary() is gone; this test would pass vacuously"
    body = owner.group(0)
    assert "inherited_event_count || 0) + 1" in body, "a branch's own events are off by one row"
    assert "s <= (next.inherited_event_count || 0)" in body, (
        "the last row of the parent's turn is assigned to the child"
    )
    assert "return i > 0 ? chain[i - 1] : null" in body

    layout = re.search(r"function branchForkLayout\(.*?\n\}", script, re.S)
    assert layout, "branchForkLayout() is gone; this test would pass vacuously"
    assert "const parent = ownerAtBoundary(chain, current, s);" in layout.group(0)
    assert "isOrigin:" in layout.group(0), "the current branch's origin is not marked"

    replay = re.search(r"function replay\(messages\)\s*\{(.*?)\n\}", script, re.S)
    assert replay, "replay() is gone; this test would pass vacuously"
    assert "flushForks(message.seq)" in replay.group(1), (
        "fork nodes are not placed at the first row after their boundary"
    )


def test_the_fork_node_is_a_centred_point_on_the_transcript():
    """The branch structure lives at the fork point, in the transcript's measure.

    The node is centred on the measure, so the drawing adds no margin column
    beside the conversation. The branch's first new prompt sits immediately below
    it; the transcript does not repeat that prompt as a second lane title. The
    local menu draws the parent and the children that leave that one boundary.
    """

    script = (WEB / "neuman.js").read_text()
    css = (WEB / "neuman.css").read_text()

    marker = re.search(r"function forkMarkerEl\(.*?\n\}", script, re.S)
    assert marker, "forkMarkerEl() is gone; this test would pass vacuously"
    assert 'el("button", "fork-node")' in marker.group(0)
    assert 'classList.add("origin")' in marker.group(0)

    menu = re.search(r"function openForkMenu\(.*?\n\}", script, re.S)
    assert menu, "openForkMenu() is gone; this test would pass vacuously"
    assert 'el("div", "fork-children")' in menu.group(0)
    assert "fork-parent" in menu.group(0)
    row = re.search(r"function forkMenuRow\(.*?\n\}", script, re.S)
    assert row, "forkMenuRow() is gone; this test would pass vacuously"
    assert "on your path" in row.group(0)

    # The marker is a hairline with a circle at its centre.
    assert ".fork-marker { display: flex; align-items: center; gap: 8px;" in css
    assert ".fork-line {" in css
    assert "height: 1px" in css


def test_the_fork_menu_draws_the_parent_and_its_children():
    """The local question is "what else starts here?", not the whole session.

    The parent row is the branch the fork came from; the child rows are the
    branches that leave that boundary. A tree of elbows is what makes the shared
    prefix visible, which a flat list of branches cannot.
    """

    css = (WEB / "neuman.css").read_text()
    script = (WEB / "neuman.js").read_text()

    assert "#fork-menu {" in css
    assert ".fork-parent::before" in css
    assert ".fork-child::before" in css
    assert ".fork-child::after" in css
    assert ".fork-child:not(:last-child)::after" in css
    assert ".fork-row.current" in css
    assert ".fork-row.path .fork-dot" in css

    # The rail is continuous: the parent line and every child's vertical segment
    # share one x, the elbows reach the dots (N177).
    assert "left: 13.5px" in css
    assert "left: -9.5px" in css
    assert "width: 22px" in css
    assert "height: auto; bottom: -2px" in css

    assert "function displayParentId" in script
    tree = re.search(r"function renderBranchMenu\(.*?\n\}", script, re.S)
    assert tree, "renderBranchMenu() is gone; this test would pass vacuously"
    assert "walk(null, 0)" in tree.group(0), "the footer branch list is flat"
    assert 'style.setProperty("--depth", depth)' in tree.group(0)


def test_the_edit_action_hangs_off_the_bubble_it_edits():
    """A control beside the bubble would leave a gap that closes under the pointer.

    The action is a child of the bubble and overlaps its corner, so the bubble's
    own hover keeps it open while the pointer moves from the text to the pencil.
    """

    css = (WEB / "neuman.css").read_text()
    script = (WEB / "neuman.js").read_text()

    assert ".edit-action {" in css
    assert "top: -12px; right: -12px;" in css, "the pencil does not sit on the bubble's corner"
    assert ".turn.user:hover > .edit-action" in css, (
        "the pencil is not shown by the bubble's own hover"
    )
    assert "pointer-events: none" in css
    assert "@media (hover: none)" in css, "a touch screen cannot reveal the pencil"

    attach = re.search(r"function attachEditAction\(.*?\n\}", script, re.S)
    assert attach, "attachEditAction() is gone; this test would pass vacuously"
    assert "node.appendChild(button)" in attach.group(0), (
        "the pencil is not a child of the bubble that shows it"
    )


def test_the_editor_is_the_bubble_and_the_tail_steps_back():
    """An overlay would leave two texts on screen and two measurements to keep.

    The bubble becomes the textarea; the transcript after it fades but stays
    readable, because the branch below still exists and is what the edit is being
    compared with.
    """

    css = (WEB / "neuman.css").read_text()
    script = (WEB / "neuman.js").read_text()

    assert ".turn.user.editing {" in css
    assert "min-width: min(300px, 88%)" in css
    assert "box-shadow: 0 0 0 1px var(--accent)" in css
    assert ".turn.user.editing ~ *" in css
    assert "opacity: 0.5" in css
    assert ".editor textarea {" in css

    assert "function editorEl" in script
    assert "commitEdit(view)" in script
    assert "cancelEdit(view)" in script

    # A press outside the bubble abandons the edit. The tail has to remain a
    # live target so the press that cancels can also do what it was aimed at.
    assert "const edit = currentView && currentView.editing;" in script
    assert "if (!edit || edit.busy) return;" in script
    assert "edit.node.contains(event.target)" in script
    assert "cancelEdit(currentView);" in script
    tail = re.search(r"\.turn\.user\.editing ~ \* \{(.*?)\}", css, re.S)
    assert tail, "the editing tail rule is gone; this test would pass vacuously"
    assert "opacity: 0.5" in tail.group(1)
    assert "pointer-events" not in tail.group(1), (
        "the dimmed tail cannot receive the press that should close the edit"
    )
    assert "user-select" not in tail.group(1)


def test_the_composer_steps_aside_while_a_bubble_is_being_edited():
    """Two live inputs in one column is a choice nobody asked for.

    The composer is disabled rather than emptied: a draft already in it is the
    reader's, and a branch switch must not destroy it. Send hides; Stop stays,
    because a running turn is still a thing to stop while its earlier prompt is
    being corrected.
    """

    script = (WEB / "neuman.js").read_text()
    css = (WEB / "neuman.css").read_text()

    paint = re.search(r"function paintComposerMode\(\)\s*\{(.*?)\n\}", script, re.S)
    assert paint, "paintComposerMode() is gone; this test would pass vacuously"
    body = paint.group(1)
    assert "const editing = Boolean(currentView && currentView.editing);" in body
    assert '$("input").disabled = editing;' in body
    assert "Finish editing above" in body
    assert '$("composer").classList.toggle("editing", editing);' in body

    assert "#composer.editing #input" in css
    assert "#composer.editing #interrupt" not in css, (
        "Stop is dimmed or disabled while an earlier prompt is being edited"
    )
    assert "#composer.editing #send" in css
    editing = re.search(
        r"#composer\.editing #input,.*?#composer\.editing #send \{(.*?)\}", css, re.S
    )
    assert editing, "the composer's editing dim rule is gone; this test would pass vacuously"
    assert "pointer-events" not in editing.group(1), (
        "a press on the composer's controls is swallowed by the dim rule"
    )


def test_the_fork_menu_dismisses_like_every_other_card():
    """A fixed card over a scrolling transcript has to close when either moves.

    The node is in the transcript, so the menu cannot be an absolutely
    positioned child of the log: it would be clipped and would scroll with the
    text. It is fixed, and therefore has to close on scroll, resize, click-away
    and Escape, or it points at a node that is no longer under it.
    """

    script = (WEB / "neuman.js").read_text()

    assert "function closeForkMenu" in script
    assert '$("log").addEventListener("scroll", closeForkMenu' in script
    assert 'window.addEventListener("resize", closeForkMenu)' in script
    assert '!$("fork-menu").contains(event.target)' in script, (
        "a click away does not close the fork menu"
    )
    escape = re.search(
        r"if \(forkMenuOpen\) \{\s*const node = forkMenuOpen\.node;\s*closeForkMenu\(\);",
        script,
    )
    assert escape, "Escape does not close the fork menu and return focus"
    assert 'aria-expanded", "false"' in script


def test_a_running_view_opens_on_its_live_edge():
    """A running session resumes mid-thought; it does not replay its history.

    The reader switched to see the step that is happening, so the view is
    pinned to the tail while a turn is in flight. An idle one restores the
    reader's own position, and an unread reply opens at the reply.
    """

    script = (WEB / "neuman.js").read_text()

    attach = re.search(r"function attachView\(.*?\n\}", script, re.S)
    assert attach, "attachView is gone; this test would pass vacuously"
    body = attach.group(0)
    assert "const pin = view.running || view.unread;" in body, (
        "a live or unread view is not pinned to its edge"
    )
    assert "box.scrollTop = followTail ? box.scrollHeight : view.scrollTop" in body, (
        "an idle view does not restore the reader's place"
    )

    show = re.search(r"function showView\(.*?\n\}", script, re.S)
    assert show, "showView is gone; this test would pass vacuously"
    parked = "parkView(previous)"
    assert parked in show.group(0), (
        "a view is dropped when the reader looks away, rather than kept in the working set"
    )


def test_the_draft_keeps_the_session_it_left():
    """New session returns to the hero without closing what is already open.

    The old `showDraft` closed the one socket, which cancelled every turn it
    carried. It then dropped an idle view outright. It now parks the view — the
    nodes stay and a running turn keeps its socket — and the window in Settings
    decides when the view goes.
    """

    script = (WEB / "neuman.js").read_text()

    show = re.search(r"function showDraft\(.*?\n\}", script, re.S)
    assert show, "showDraft is gone; this test would pass vacuously"
    body = show.group(0)
    assert "parkView(currentView)" in body, "the draft drops the session it left"
    assert "discardView(" not in body, "the draft still closes what it left"
    assert "socket.onclose = null" not in body, "the draft still closes every socket"


def test_the_draft_transcript_takes_no_notes():
    """A note is a line in a conversation, and a draft has none.

    The log row is zero-height in this state, so a note appended to it was not
    merely out of place — it was clipped to a sliver of text at the very top of
    the window, above the heading and the box. Choosing a model drew "Using …"
    there. Feedback about something staged before the first message belongs to
    the control that did it, so `note` draws nothing with no view to draw into;
    the model chooser still says which model is in use in a real session, and a
    background view's note goes to its own holder even while the draft is open.
    """

    script = (WEB / "neuman.js").read_text()

    note = re.search(r"function note\([^)]*\)\s*\{(.*?)\n\}", script, re.S)
    assert note, "note() is gone; this test would pass vacuously"
    body = note.group(1)
    assert "if (!renderView) return null;" in body, (
        "a note is still appended to the empty draft log"
    )
    assert "drafting()" not in body, (
        "the guard is on the draft alone, so a background view's note is lost"
    )

    choose = re.search(r"async function chooseModel\([^)]*\)\s*\{(.*?)\n\}", script, re.S)
    assert choose, "chooseModel is gone; this test would pass vacuously"
    assert "note(" in choose.group(1), (
        "a model change in a session no longer says what is in use"
    )


def test_a_session_stays_in_the_working_set_for_the_window_it_was_given():
    """Active is more than what is live, and the account says how much more.

    The section held only a turn running or a reply unread, so an idle session
    vanished the moment the reader looked elsewhere — and coming back re-fetched
    and rebuilt it. A view now stays for `active_minutes` after its last use; a
    running turn and an unread reply stay whatever the clock says, because
    neither is a time since use and time is not an answer to either.

    The window is read from the preference on every question rather than copied
    at sign-in: the control applies to views that are already open.
    """

    script = (WEB / "neuman.js").read_text()

    active = re.search(r"function keepViewWarm\(.*?\n\}", script, re.S)
    assert active, "keepViewWarm is gone; this test would pass vacuously"
    body = active.group(0)
    assert "view === currentView || view.running || view.unread" in body, (
        "a live or unread view is no longer exempt from the window"
    )
    assert "warmWindowOpen(view)" in body, "a kept view ignores the account's window"

    window = re.search(r"function warmWindowOpen\(.*?\n\}", script, re.S)
    assert window, "warmWindowOpen is gone; this test would pass vacuously"
    assert "warmWindowMs()" in window.group(0), "the window's predicate has no clock"

    ms = re.search(r"function warmWindowMs\(.*?\n\}", script, re.S)
    assert ms, "warmWindowMs is gone; this test would pass vacuously"
    assert "interfacePrefs.active_minutes" in ms.group(0), (
        "the window is copied at sign-in, so a change does not reach open views"
    )

    park = re.search(r"function parkView\(.*?\n\}", script, re.S)
    assert park, "parkView is gone; this test would pass vacuously"
    parked = park.group(0)
    assert "view.touchedAt = Date.now()" in parked, (
        "the window starts when the session was opened, not when it was left"
    )
    assert "if (view.running) return;" in parked, "parking closes a running turn's socket"
    assert "closeViewSocket(view)" in parked, "an idle view keeps a socket nobody needs"
    assert "discardView(view)" in parked, (
        "0 minutes leaves the view in memory until the next tick"
    )

    # The sweep is the only thing that takes a view back out, and a view that goes
    # is still a session: the rail is redrawn so its row returns to its date group.
    assert "if (keepViewWarm(view)) continue;" in script, "the sweep drops live views"
    assert "if (dropped) renderSessions();" in script, (
        "an expired view's row is not returned to the past list"
    )


def test_the_folded_rail_carries_the_running_and_unread_count():
    """With the list out of sight, the grip is the only surface left.

    A turn started and then folded away still finishes; without a count on the
    grip, the reply would appear in no visible place. Amber with an exclamation
    for an approval, accent for a running turn, green for a reply waiting.
    """

    script = (WEB / "neuman.js").read_text()
    markup = (WEB / "index.html").read_text()

    assert 'id="sessions-grip-badge"' in markup, "the grip has no badge"
    badge = re.search(r"function paintGripBadge\(.*?\n\}", script, re.S)
    assert badge, "paintGripBadge is gone; this test would pass vacuously"
    body = badge.group(0)
    assert "stateView.running" in body and "stateView.unread" in body, (
        "the badge counts only one of the two live states"
    )
    assert "rail-collapsed" in body, "the badge is not tied to the folded rail"
    assert '"var(--warn)"' in body and '"var(--ok)"' in body, (
        "the badge cannot distinguish an approval from a waiting reply"
    )


def test_an_active_row_names_its_state_in_words_and_colour():
    """The dot is the glance; the word is the same fact for everyone else.

    A pulse alone is unreadable to anyone who cannot see the hue, and `needs
    you` is the one state that must not depend on noticing anything subtle.

    There is no clock. The row carried a live counter for a running turn and a
    "3m ago" for a recent one, and both were motion nobody reads on a line
    whose job is to say what is happening — so the rail is scanned, not watched,
    and nothing in it needs a timer.
    """

    script = (WEB / "neuman.js").read_text()
    styles = (WEB / "neuman.css").read_text()

    info = re.search(r"function viewStateInfo\(.*?\n\}", script, re.S)
    assert info, "viewStateInfo is gone; this test would pass vacuously"
    body = info.group(0)
    for word in ('"thinking"', '"needs you"', '"finished"', '"reply ready"'):
        assert word in body, f"the active row cannot say {word}"
    assert "elapsed" not in body, "the row carries a clock again"
    assert "clockSince" not in script, "a clock is being counted somewhere in the rail"
    assert "#sessions .state-line .elapsed" not in styles, "the clock's rule is still here"

    paint = re.search(r"function paintActiveRow\(.*?\n\}", script, re.S)
    assert paint, "paintActiveRow is gone; this test would pass vacuously"
    assert "row.setAttribute(" in paint.group(0), "the row has no accessible name"
    assert "paintActiveGroupNote()" in script, "the heading count cannot follow a phase"

    for rule in (
        "#sessions .row.state-thinking .live-dot",
        "#sessions .row.state-approval .live-dot",
        "#sessions .row.state-done .live-dot",
        "#sessions .state-line",
    ):
        assert rule in styles, f"{rule} is not styled"
    assert "#sessions .live-dot { animation: none !important; }" in styles, (
        "the pulse survives prefers-reduced-motion"
    )

    # The menu item toggles through the same PATCH as the other row fields, and
    # states the effect rather than the state, so the current value never has to
    # be inferred from the wording.
    pinned = re.search(r"function setPinned\(.*?\n\}", script, re.S)
    assert pinned, "setPinned is gone; this test would pass vacuously"
    assert 'api("PATCH", `/api/sessions/${session.id}`, { pinned })' in pinned.group(0), (
        "a pin does not go through the route the other row fields use"
    )
    assert '"Pin to top"' in script and '"Unpin"' in script, (
        "the row menu cannot pin or cannot undo it"
    )


def test_the_order_of_the_list_is_the_servers_and_the_reader_chooses_it():
    """Ordering is a query, not a sort in the browser, for the filter's reason.

    `list_for_account` caps at fifty. Sorting those fifty by creation and calling
    it "recently used" is a different fifty from the fifty most recently used,
    and it looks right until an account outgrows the cap — the same shape of
    limitation the search had before it went server-side (D52).
    """

    script = (WEB / "neuman.js").read_text()
    markup = (WEB / "index.html").read_text()

    load = re.search(r"async function loadSessions\(.*?\n\}", script, re.S)
    assert load, "loadSessions is gone; this test would pass vacuously"
    body = load.group(0)
    assert "sort=${sessionsSort}" in body, "the chosen order never reaches the server"
    assert ".sort(" not in body, "the client re-sorts a list the server already ordered"

    # The two dates the menu offers, and the default it opens with. `Last
    # activity` is the newest of the session's creation, its newest event and its
    # closing — the same fact the retention clock reads.
    assert 'data-sort="used"' in markup and 'data-sort="created"' in markup
    checked = re.search(r'data-sort="used"[^>]*aria-checked="(\w+)"', markup)
    assert checked and checked.group(1) == "true", (
        "the menu opens with a different default from the one the list uses"
    )
    default = 'let sessionsSort = SESSION_SORTS.includes(savedSort) ? savedSort : "used";'
    assert default in script, "the default order is gone, or an unknown stored value is trusted"
    assert 'const SORT_STORAGE = "neuman.sessions.sort";' in script, (
        "the choice is not remembered per browser, like the rail's width"
    )


def test_the_new_session_button_is_quiet_and_still_the_primary_act():
    """The rail's primary act is a tint, not a filled slab.

    It began as a neutral button between a wordmark and a search field, so the
    one control that starts something looked like the two that find something.
    It was made `.primary`, and the accent fill then made it the loudest thing
    in the panel — every other control in the head is a hairline beside it. The
    answer is the middle: `--tint-accent` with accent ink keeps a box (so it is
    still a button) and one hue (so it is still an action) without the slab.
    """

    src = WEB
    markup = (src / "index.html").read_text()
    styles = (src / "neuman.css").read_text()

    tag = re.search(r'<button[^>]*id="new"[^>]*>.*?</button>', markup, re.S)
    assert tag, "the New-session button is gone"
    assert 'class="primary"' not in tag.group(0), (
        "the accent fill is back; it out-shouts every control around it"
    )
    assert "<svg" in tag.group(0), "the plus is typed rather than drawn"
    assert 'stroke="currentColor"' in tag.group(0), (
        "the icon does not take the button's ink, so it cannot follow its state"
    )

    rule = re.search(r"#sessions-head #new \{([^}]*)\}", styles)
    assert rule, "#new has no rule; this test would pass vacuously"
    assert "background: var(--tint-accent)" in rule.group(1), (
        "the button is not the tinted surface"
    )
    assert "color: var(--accent)" in rule.group(1), "the button's ink is not the accent"

    size = re.search(r"#sessions-head #new svg \{([^}]*)\}", styles)
    assert size, "the icon has no size of its own"
    assert "width: 14px" in size.group(1), (
        "a 16-unit viewBox in a text run sets its own baseline unless it is sized"
    )

    # The login button is the one filled button left, and it keeps the hover a
    # filled surface needs — `button:hover` alone only moves a border that is
    # already the accent.
    assert "button.primary:hover:not(:disabled)" in styles, (
        "the filled login button has no hover of its own, so it reads as dead"
    )


def test_the_sessions_head_is_the_panel_button_not_a_caption():
    """`#sessions-head` was declared twice, and the second rule won.

    It applied 11px uppercase caption typography to the container of the panel's
    primary button, and `button { font: inherit }` carried that onto "New
    session". One rule now.
    """

    styles = (WEB / "neuman.css").read_text()

    rules = re.findall(r"#sessions-head\s*\{[^}]*\}", styles)
    assert len(rules) == 1, f"#sessions-head is declared {len(rules)} times"
    assert "text-transform: uppercase" not in rules[0], (
        "the caption typography is back on the panel's button container"
    )


def test_the_account_is_a_circle_in_the_top_right_corner():
    """The account is a round avatar, not a chip in the rail or the header.

    It is the one control with no region to belong to — nothing in the transcript,
    the rail or the composer owns "who am I" — so it is fixed to the corner, where
    a circle carrying initials reads as the signed-in person. The face shows
    initials rather than the name, because the face has to stay one size for every
    account; the name is in `title` and `aria-label`.
    """

    src = WEB
    markup = (src / "index.html").read_text()
    styles = (src / "neuman.css").read_text()
    script = (src / "neuman.js").read_text()

    # Outside the rail: folding the rail must not take the account with it.
    rail = re.search(r'<aside id="sessions".*?</aside>', markup, re.S).group(0)
    assert 'id="account-toggle"' in markup
    assert 'id="account-toggle"' not in rail
    assert 'id="account-initials"' in markup
    assert "function initials(" in script
    assert '$("account-initials").textContent = initials(' in script

    circle = re.search(r"#account-toggle\s*\{([^}]*)\}", styles)
    assert circle, "#account-toggle has no rule; this test would pass vacuously"
    block = circle.group(1)
    assert "position: fixed" in block
    assert "border-radius: 50%" in block, "the account chip is not a circle"
    assert "right:" in block and "top:" in block

    # Flat, like everything else here. The chip once had a 155° gradient and a
    # glow ring on hover — the only gradient and the only glow in the
    # stylesheet, which made the most prominent control the one that did not
    # look like the appliance. Asserted on the whole file rather than the rule,
    # because the point is that the theme has none of either: a second one
    # appearing anywhere is the same drift returning by another door.
    #
    # A `mask-image` is not a paint: it sets an alpha channel and draws no
    # colour, so the thought card's band is not the drift this is watching for.
    # Stripped by declaration rather than by file, so a gradient that *paints*
    # anywhere still fails.
    painted = re.sub(r"(?:-webkit-)?mask-image:\s*[^;]+;", "", styles)
    assert "linear-gradient" not in painted, "a gradient is back; the theme is flat"
    assert "radial-gradient" not in painted
    assert "box-shadow: 0 0 0 3px" not in styles, "a glow ring is back on a control"

    # The menu it used to open is gone — the chip opens the settings dialog
    # directly — so its rule must be gone too rather than left styling nothing.
    assert "#account-menu" not in styles, "a rule survives for the removed account menu"
    chip = re.search(r'<button[^>]*id="account-toggle"[^>]*>', markup, re.S)
    assert chip and 'aria-haspopup="dialog"' in chip.group(0), (
        "the chip opens a dialog now, and must say so rather than claiming a menu"
    )


def test_settings_is_a_centred_modal_not_a_column_or_a_corner_card():
    """Four tabs need two axes, which a corner card cannot give them.

    It was a full-height resizable column, then a 360px card pinned under the
    account chip — right while the content was one read-only policy list. With
    interface preferences, the account's own instructions, a permissions reading
    and usage it needs a sidebar *and* a body, and anchoring 640px to a corner
    control puts it off the edge of a narrow window.

    The grip and the width variable from the column era must stay gone, and the
    dialog must not fill the window: it is opened mid-conversation, and the
    transcript staying visible around it is how you keep your place.
    """

    src = WEB
    markup = (src / "index.html").read_text()
    styles = (src / "neuman.css").read_text()

    assert 'id="settings-grip"' not in markup
    assert "#settings-grip" not in styles
    assert "--settings-width" not in styles

    block = re.search(r"\n  #settings \{(.*?)\n  \}", styles, re.S)
    assert block, "#settings has no rule; this test would pass vacuously"
    rule = block.group(1)
    assert "position: fixed" in rule
    # Centred by `inset: 0` plus `margin: auto` rather than by a translate, so a
    # focused control inside cannot land on a half-pixel and blur.
    assert "inset: 0" in rule and "margin: auto" in rule, "the dialog is not centred"
    # Bounded on both axes, and by `min()` against the viewport so it shrinks on
    # a small window instead of overflowing it.
    assert "width: min(" in rule and "height: min(" in rule
    assert "100vw" in rule and "100vh" in rule, "the dialog does not yield to a small window"

    # The scrim: a real element, so a press outside has a target and is blocked
    # from reaching what is underneath.
    assert 'id="scrim"' in markup
    scrim = re.search(r"\n  #scrim \{(.*?)\}", styles, re.S)
    assert scrim and "inset: 0" in scrim.group(1), "the scrim does not cover the window"


def test_the_reasoning_preference_governs_unfolding_not_recording():
    """The Interface toggle collapses the row; it never suppresses the thinking.

    A preference that stopped building the row would hide the fact that the
    model thought at all, and the transcript is a record. So the row is always
    created and always filled — the preference decides only whether it opens
    itself, which is the part that pushes the reply down the window every turn.

    Stored in `localStorage` rather than the database, like the rail's width: it
    describes this browser, and syncing it would let one tab change another's
    reading.
    """

    src = WEB
    markup = (src / "index.html").read_text()
    script = (src / "neuman.js").read_text()

    assert 'id="pref-hide-reasoning"' in markup
    assert "localStorage" in script and "hideReasoning" in script

    # The row is built unconditionally; only the unfold is gated.
    frame = re.search(r'case "reasoning":(.*?)break;', _handle(script), re.S)
    assert frame, "the reasoning frame handler is gone; this test would pass vacuously"
    body = frame.group(1)
    assert "reasoningBlock()" in body
    assert "hideReasoning()" in body, "the preference does not reach the reasoning row"
    assert "_body.dataset.raw" in body, (
        "the thinking is no longer accumulated, so the preference is suppressing it"
    )


def test_the_caption_above_the_bar_is_the_spend_and_nothing_else():
    """A model with no price gets no caption, so the gauge stands alone.

    The status line is a footnote to the message box, and the caption over the
    bar is the one figure the gauge cannot show: `92k in · 92k out · 184k total
    tokens` put three labelled values above an 80px gauge and pushed the spend
    off the end of the line.

    `↓2.4k` was the caption where no rate could price the turn, on the argument
    that output tokens are the only cost a free model has — which is a local
    model. It answered a question nobody asked: the bar is the reading, and a
    lone count above it, sharing its line with no other figure, reads as another
    control on the status line rather than as the bar's caption. So an unpriced
    turn shows nothing, and the gauge sits alone.

    That is also how "local" is recognised here, and it is not a proxy:
    `session_cost_usd` is sent only when `turn_cost` could price the step, and it
    declines for a model with no declared rates. The exact counts are not
    deleted — they are on the caption's title, where `usageTitle` has always put
    them.
    """

    script = (WEB / "neuman.js").read_text()
    code = re.sub(r"//.*", "", script)

    match = re.search(r"function usageLine\(usage, frame\)\s*\{(.*?)\n\}", code, re.S)
    assert match, "usageLine is gone; this test would pass vacuously"
    body = match.group(1)
    assert "money(" in body, "the spend is no longer the caption"
    assert "↑" not in body and "↓" not in body, "a token count is back over the gauge"
    assert "total" not in body, "the redundant total is back on the status line"
    assert "compactTokens" not in code, (
        "the arrow helper outlived its only caller; dead code is not a reading"
    )

    title = re.search(r"function usageTitle\(usage\)\s*\{(.*?)\n\}", code, re.S)
    assert title, "usageTitle is gone; the numbers were deleted rather than moved"
    assert "toLocaleString" in title.group(1), "the title is not the exact count"

    # Clearing the line on a new session has to clear the title with it, or the
    # exact figures of the last session sit on an empty status line.
    clear = re.search(r"function clearUsage\(\)\s*\{(.*?)\n\}", code, re.S)
    assert clear, "clearUsage is gone; the text and title can drift apart"
    assert '"usage"' in clear.group(1) and ".title" in clear.group(1), (
        "clearUsage leaves the title behind"
    )


def test_the_context_bar_reads_the_field_the_server_sends():
    """The bar's denominator crosses three files, and two of them are not Python.

    The server puts `context_tokens` on the `usage` frame; the client reads it
    to size the bar. A field renamed on one side and not the other leaves a bar
    that never appears — the quiet failure mode of every client/server pair in
    this tree (N38/N48), and here it would hide the one indicator added to make
    context exhaustion visible (N86).

    Matched narrowly, because N81 was five rounds of a test passing against the
    prose that explained the string it was grepping for.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()
    styles = (src / "neuman.css").read_text()

    # The client must take the capacity from the frame, not from a constant.
    assert "showContext(frame.usage, frame.context_tokens, frame.compacts_above)" in script, (
        "the usage handler does not pass the window and the compaction point to the bar"
    )

    # The bar and its fill exist, and the fill is what gets sized.
    assert 'id="context-bar"' in markup
    assert 'id="context-fill"' in markup
    assert '$("context-fill").style.width' in script

    # And the denominator is the compaction point, not the model's window. Those
    # are different numbers and only one of them is being approached: the window
    # reserves the reply ceiling, a margin and the compaction reserve before the
    # conversation gets any, so a bar against it sat a little under half full at
    # the moment history was summarized (N119, N120).
    assert "compactsAbove && compactsAbove > 0 ? compactsAbove : capacity" in script, (
        "the bar does not measure against the compaction point"
    )
    assert "used / ceiling" in script

    # Both threshold classes are styled, or a full context looks like an empty
    # one. The classes are set in JS and coloured in CSS — different files.
    assert "#context-bar.warn #context-fill" in styles
    assert "#context-bar.bad  #context-fill" in styles
    assert 'classList.toggle("warn"' in script
    assert 'classList.toggle("bad"' in script


def test_the_context_bar_hides_without_a_window():
    """No denominator means no bar, rather than a bar against a guess.

    A window the operator never recorded is exactly the state that caused the
    bug: budgeting against an unrecorded number. Drawing a reassuring bar in
    that case would reintroduce it as a UI affordance.
    """

    src = WEB
    script = (src / "neuman.js").read_text()

    # The guard, matched on its own line rather than on the function: the
    # function would still be present with the guard deleted. It tests
    # `ceiling`, which is the compaction point where one is known and the
    # window otherwise — so a model with neither still draws nothing.
    assert "if (!ceiling || ceiling <= 0 || used == null) {" in script, (
        "the bar does not refuse to draw without a denominator"
    )


def test_the_approval_picker_respects_the_ceiling():
    """The ceiling has to disable choices, not merely be sent.

    The server refuses an out-of-range mode either way, but a UI that offered
    all three and showed a 403 on click would be advertising something the gate
    denies — the thing `test_advertisement_equals_the_gate` exists to prevent on
    the tool side.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    styles = (src / "neuman.css").read_text()

    assert "policy.auto_accept_max" in script, "the picker never reads the ceiling"
    assert "button.disabled = true" in script, "an out-of-range mode is still clickable"
    # And disabled is visually distinct, or the limit is invisible.
    assert "#approval-choices button:disabled" in styles


def test_the_approval_picker_shows_the_mode_names():
    """Colour alone would make `never`/`safe`/`yolo` unsearchable from the UI.

    Those are the strings the audit chain, `neuman-admin policy show` and the
    policy view all use, so someone reading any of them needs to find the same
    word in Settings.

    This asserts the *mode name reaches the label*, not the exact expression that
    puts it there. It used to pin the literal `${entry.colour} · ${entry.label}`,
    which described one particular rendering — plain-language labels prefixed with
    a colour word — and broke when the picker moved to the bare mode name beside a
    colour swatch. That change satisfied this test's actual purpose *better*, since
    the searchable word is now the label rather than sitting next to it, and the
    test failed anyway. A test that pins the implementation of a requirement fails
    on improvements to it.
    """

    script = (WEB / "neuman.js").read_text()

    assert 'el("span", "label", entry.mode)' in script, (
        "the button label does not carry the mode name, so `never`/`safe`/`yolo` "
        "cannot be found from the UI"
    )
    # The colour is a swatch beside the word, not a replacement for it.
    assert 'el("span", "dot")' in script, "the colour cue is gone"
    assert "button.title = `${entry.mode}" in script, "the mode name is not in the title either"


def test_the_tool_card_hides_its_controls_until_approval_is_required():
    """The two halves of the pair, in the client, in different files.

    The row is built hidden and revealed on `tool_approval_required`; the
    decision removes it. Built-then-revealed rather than created on demand, so
    one element has one lifecycle — and asserted here because the frame that
    reveals it is emitted by Python and read by JavaScript, which is the drift
    this suite exists to catch.
    """

    src = WEB
    script = (src / "neuman.js").read_text()

    assert 'el("div", "ask hidden")' in script, (
        "the approve/deny row is shown before anyone is asked"
    )
    assert 'case "tool_approval_required":' in script, (
        "the client never learns that a human is being asked"
    )
    assert 'ask.classList.remove("hidden")' in script, "the row is hidden and never revealed"


def test_the_tool_card_names_who_decided_rather_than_a_boolean():
    """`running · false` is a field's value, not a sentence.

    And the obvious misreading — "running: false" — says the opposite of what
    the frame means, which is worse than being merely terse.
    """

    script = (WEB / "neuman.js").read_text()

    assert 'frame.consent ? "you" : "auto"' in script
    assert "`running · ${frame.consent}`" not in script


def test_the_client_shows_a_truncated_reply():
    """Server emits it, client renders it, and the bubble is closed first.

    Without closing the bubble the continuation appears to carry on the same
    sentence across the break, which reads as one garbled reply rather than two
    halves of a thought.
    """

    script = (WEB / "neuman.js").read_text()

    assert 'case "truncated":' in script, "the client ignores the truncation notice"
    # The handler must reset the bubble, not just print a line.
    handler = _handle(script).split('case "truncated":', 1)[1].split("break;", 1)[0]
    assert "agentTurn = null" in handler, "the bubble is not closed before continuing"


def test_a_turn_that_can_be_resumed_says_how():
    """`truncated` and `max_iterations` both mean "there is more to do".

    The next thing to type is the same for both, so the message says it rather
    than leaving a bare reason word for the reader to interpret — this bug was
    invisible for weeks partly because "stop" sounded like success.
    """

    script = (WEB / "neuman.js").read_text()

    assert 'frame.reason === "truncated" || frame.reason === "max_iterations"' in script
    assert 'say "continue" to pick up where it stopped' in script


def test_the_client_decides_to_send_a_level_from_the_declaration_not_the_dom():
    """The bug: switching models sent the previous model's level.

    A `<select>` keeps its old `value` until the painter rebuilds its options, so
    a visibility class and a stale read disagree for exactly one turn. Going from
    qwen (which declares levels) to glm (which declares none) sent qwen's level
    to a model that takes none, and the turn ended in `reasoning_error` — a real
    failure on a real appliance, with nothing wrong in the config.

    The class is a rendering fact; whether a model takes a level is a data fact.
    Asserting the source because the alternative is a browser, and because the
    tempting fix — reading `classList` — is what broke.
    """

    script = (WEB / "neuman.js").read_text()

    send = script[script.index('const frame = { type: "message"') :][:600]

    # The level comes from a decision about the model, not from the DOM. Asserted
    # as "calls the chooser" rather than "contains `reasoningByModel[model]`",
    # because that spelling was the inlined form and pinning it failed the moment
    # the same logic moved into a named function — testing where the decision is
    # made, not how it is spelled at the call site.
    assert "chosenLevel(model)" in send, (
        "the send path no longer asks the declaration for this model's level; a "
        "stale select value can reach a model that declares no levels"
    )
    assert "classList" not in send, (
        "the send path is gating on the control's visibility again — that is the "
        "rendering state, not whether this model takes a level"
    )

    # And the chooser is the thing that reads the declaration and refuses a level
    # the model does not declare. Without this half, `chosenLevel` could be
    # rewritten to trust `reasoningChoice` and the assertion above would hold.
    chooser = script[script.index("function chosenLevel(") :][:600]
    assert "reasoningByModel[name]" in chooser, (
        "chosenLevel no longer consults the model's own declaration"
    )
    assert "levels.includes(" in chooser, (
        "chosenLevel no longer validates the remembered choice against the "
        "declared levels; a stale choice would be sent and refused mid-turn"
    )


def test_the_reasoning_level_lives_with_the_composer_not_the_model_list():
    """One control, on the status line beside approval, and it follows the model.

    The level is a choice about how the next turn runs, like approval, not about
    which model is installed — so it lives on the status line, beside the model
    control it is declared by. The levels are the *model's* declaration, so the
    chip is rebuilt from `reasoningByModel` when the model changes and hidden
    entirely for a model that declares none. Asserted from the source because the
    alternative is a browser, and because the failure to guard against is two
    controls (panel and status line) disagreeing about the level in force.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()
    styles = (src / "neuman.css").read_text()
    code = re.sub(r"//.*", "", script)

    # The control exists where a message is written, and nowhere else.
    assert 'id="reasoning-chip"' in markup, "there is no reasoning chip on the status line"
    assert 'id="reasoning-menu"' in markup, "the chip has no popover"
    assert "reasoning-row" not in styles, "the models panel still styles a level row"
    assert "reasoning-row" not in code, "the models panel still draws a level row"

    # It is rebuilt from the model's declaration, so a model that declares no
    # levels gets no control that would send a value the server refuses.
    match = re.search(r"function paintReasoning\(\)\s*\{(.*?)\n\}", code, re.S)
    assert match, "paintReasoning is gone; this test would pass vacuously"
    body = match.group(1)
    assert "reasoningByModel[model]" in body, "the levels do not follow the model"
    assert "levels.length" in body, "a model with no levels still gets a chip"

    # One display: the model chip no longer repeats the level the composer shows.
    chip = re.search(r"function paintGpuChip\(gpu\)\s*\{(.*?)\n\}", code, re.S)
    assert chip, "paintGpuChip is gone; this test would pass vacuously"
    assert "chosenLevel" not in chip.group(1), (
        "the model chip still repeats the reasoning level the composer shows"
    )

    # The popover is dismissed and marked expanded like every other card.
    assert '["reasoning-menu", ["reasoning-chip"]]' in code, (
        "the popover is not in CARDS, so it does not close on click-away, Escape, "
        "or report aria-expanded"
    )


def test_the_reasoning_mark_carries_the_level_rather_than_decorating_the_chip():
    """A mark that never changes is not a mark, it is decoration.

    The chip carried a fixed sparkle and the word beside it carried the whole
    state. The approval dot next to it is the contrast: it *is* the state, which
    is why it is worth a glance. This replaces the sparkle with a depth meter —
    one bar per live level, filled to the one in force — because depth is the
    only axis the levels have and a count carries it without colour.

    Colour is deliberately absent, and that is the part worth asserting: the
    approval palette is a risk ramp (red stops everything, green stops nothing),
    and a level is not good or bad. Spending those three hues here would make
    them mean less where they must mean danger.

    The one fact the client cannot derive from the declaration is which
    spelling means off, so it keeps a list — and the list has to agree with the
    server's, which owns the dialects. That is asserted below rather than
    trusted, because two names for the same thing drifting apart is the shape
    of every bug this file exists to catch.
    """

    src = WEB
    script = (src / "neuman.js").read_text()
    markup = (src / "index.html").read_text()
    styles = (src / "neuman.css").read_text()
    code = re.sub(r"//.*", "", script)

    # The constant mark is gone from the markup and the stylesheet both, so it
    # cannot come back through either door. Asserted on the class rather than on
    # the word, so a comment that says which mark this replaced is not a failure.
    assert 'class="spark"' not in markup, "the chip still draws a mark that never changes"
    assert ".spark" not in styles, "the stylesheet still styles a constant mark"
    assert 'class="depth"' in markup, "the chip has nowhere to paint the meter"

    # The ramp: filled to the level's position among the *live* levels, and
    # empty for an off spelling, which is absence of thinking rather than a
    # step of it. A ramp where `off` drew one bar would read as low effort.
    depth = re.search(r"function reasoningDepth\(levels, level\)\s*\{(.*?)\n\}", code, re.S)
    assert depth, "reasoningDepth is gone; this test would pass vacuously"
    assert "OFF_LEVELS.has(level)" in depth.group(1), (
        "an off level is drawn as a step of thinking"
    )
    assert "indexOf(level) + 1" in depth.group(1), (
        "the fill does not follow the level's position in the declaration"
    )

    # Painted for the chip and for every row of the popover, so the ramp is
    # visible at the moment of choosing and not only after.
    paint = re.search(r"function paintReasoning\(\)\s*\{(.*?)\n\}", code, re.S)
    assert paint, "paintReasoning is gone; this test would pass vacuously"
    assert "reasoningDepth(levels, chosen)" in paint.group(1), (
        "the chip's meter does not follow the level in force"
    )
    assert "reasoningDepth(levels, level)" in paint.group(1), (
        "the popover rows draw no ramp, so the order of the levels is invisible"
    )

    # The mark is decorative and the word is the carrier, so the button has to
    # name itself: an aria-hidden glyph plus a bare level word is a control
    # announced as "medium".
    assert "aria-label" in paint.group(1), (
        "the chip's only text is the level word, with the mark aria-hidden"
    )

    # The off spellings, checked against the server's list rather than assumed.
    server = (
        Path(__file__).parent.parent.parent
        / "neuman-server"
        / "src"
        / "neuman_server"
        / "decision"
        / "harness"
        / "reasoning.py"
    ).read_text()
    theirs = re.search(r"_OFF_LEVELS = frozenset\(\{([^}]*)\}\)", server)
    assert theirs, "reasoning.py has no _OFF_LEVELS; this test would pass vacuously"
    ours = re.search(r"const OFF_LEVELS = new Set\(\[([^\]]*)\]\)", code)
    assert ours, "the client has no OFF_LEVELS; `off` would draw a filled bar"
    assert set(re.findall(r'"([^"]+)"', ours.group(1))) == set(
        re.findall(r'"([^"]+)"', theirs.group(1))
    ), (
        "the client and the server disagree about which levels mean 'do not "
        "think'; the meter would draw one of them as a step of thinking"
    )


def test_no_rule_in_the_stylesheet_carries_a_colour_literal():
    """A colour outside the palette is a colour only one theme gets right.

    This is the rule the palette exists to enforce. The stylesheet had 35 hex
    literals in rules plus 11 black shadows — `#22262e` seventeen times as a
    hover surface the palette had no name for — and every one of them would have
    been correct in dark and wrong in light, invisibly, until someone switched.

    Declarations *inside* a `:root` block are the exception, because that is
    where a palette is defined; everywhere else a colour must come through
    `var()`. The scrim is the one allowed literal in a rule and is asserted by
    name below, so the exemption cannot quietly widen.

    Every layout sheet is checked, not just the chat window's: the
    administration page loads the palette and its own `admin.css` and nothing
    else (N158), so a literal there is the same bug one document over, and the
    task view's `tasks.css` is drawn in the chat window's tokens.
    """

    offenders: list[str] = []
    for sheet in ("neuman.css", "admin.css", "tasks.css"):
        css = (WEB / sheet).read_text()

        # Comments carry prose about colours — including the literals this
        # change removed — and are not rules.
        bare = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", bare):
            if selector.strip().startswith(":root"):
                continue
            for line in body.split(";"):
                if re.search(r"#[0-9a-fA-F]{3,8}\b", line) or re.search(
                    r"\brgba?\(\s*\d", line
                ):
                    offenders.append(f"{sheet}: {selector.strip()} {{{line.strip()}}}")

    assert not offenders, (
        "colour literals outside the palette — these will be wrong in at least "
        "one theme:\n  " + "\n  ".join(offenders)
    )

    # The shadows go through a token too, which is the half of this that is easy
    # to miss: a black shadow is correct on a dark palette and reads as dirt on
    # a light one, so `--shadow` is a palette value like any other.
    css = (WEB / "neuman.css").read_text()
    bare = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    assert "rgb(var(--shadow)" in css, "shadows are not themed"
    assert "rgb(0 0 0" not in bare, "a hardcoded black shadow is back"


def test_every_palette_is_legible():
    """Contrast is a property of a pair, which is why the tokens are semantic.

    The whole argument for naming tokens by *role* rather than by index is that
    it lets this test exist: `--text` on `--bg` is a pair the names already
    describe, so one table checks every palette instead of each theme having to
    re-derive which combinations are load-bearing.

    WCAG AA: 7:1 for prose (AAA for body text, which is what a transcript is),
    4.5:1 for everything else carrying text. The non-text pairs at the end use
    low floors on purpose — a border and an inert dot have to be *visible*, not
    readable, and holding them to 4.5:1 would force a palette to shout.
    """

    css = (WEB / "palette.css").read_text()

    palettes = _palette_blocks(css)
    assert set(palettes) == {
        "dark",
        "light",
        "nord",
        "everforest-dark",
        "everforest-light",
        "gruvbox-dark",
        "gruvbox-light",
    }, f"palettes found: {sorted(palettes)}"
    dark = palettes["dark"]

    # (ink, surface, floor). Every pair here is one the stylesheet actually
    # renders; a pair that does not occur is not worth constraining, because the
    # cost of an invented constraint is a palette distorted to satisfy it.
    pairs = [
        ("--text", "--bg", 7.0),
        ("--text", "--panel", 7.0),
        ("--text", "--surface-raised", 4.5),
        ("--text", "--surface-inset", 7.0),
        ("--text", "--hover", 7.0),
        ("--text", "--tint-accent", 7.0),
        # The selected session's row is this tint, and the date and snippet
        # beside its title are still `--dim` once it is selected. This is the
        # tightest pair the tint has to keep legible: gruvbox dark measures
        # 4.54:1, four hundredths above the floor.
        ("--dim", "--tint-accent", 4.5),
        ("--dim", "--bg", 4.5),
        ("--dim", "--panel", 4.5),
        # The shell prompt's punctuation and a fenced block's comment gutter.
        ("--dim", "--surface-inset", 4.5),
        ("--work", "--panel", 4.5),
        ("--accent", "--panel", 4.5),
        ("--accent", "--bg", 4.5),
        ("--accent", "--hover", 4.5),
        # The shell prompt's user and host, and a fenced block's keywords.
        ("--accent", "--surface-inset", 4.5),
        # A fenced block's strings and numbers sit in the inset well too.
        ("--ok", "--surface-inset", 4.5),
        ("--warn", "--surface-inset", 4.5),
        # The rail's New-session button: accent ink on its own tint, which is
        # what makes it a quiet primary act rather than a filled one. The
        # selected session row's title wears the same pair.
        ("--accent", "--tint-accent", 4.5),
        # The same button under the pointer. How far the tint can deepen before
        # its own ink loses contrast is a property of the palette, so the
        # percentage is a token and this pair is checked like any other.
        ("--accent", "--tint-accent-hover", 4.5),
        ("--warn", "--panel", 4.5),
        ("--warn", "--hover", 4.5),
        ("--warn", "--tint-warn", 4.5),
        ("--bad", "--panel", 4.5),
        ("--bad", "--hover", 4.5),
        ("--bad", "--tint-bad", 4.5),
        # A failed shell command prints in red on the terminal's inset surface.
        ("--bad", "--surface-inset", 4.5),
        ("--ok", "--panel", 4.5),
        ("--violet", "--panel", 4.5),
        ("--ink-on-accent", "--accent", 4.5),
        # The folded rail's count badge. It is filled with whichever state it is
        # reporting, so the same ink has to be legible on all three.
        ("--ink-on-accent", "--warn", 4.5),
        ("--ink-on-accent", "--ok", 4.5),
        # Visible, not readable.
        ("--line", "--panel", 1.35),
        ("--inert", "--panel", 1.8),
        ("--surface-raised", "--panel", 1.05),
    ]

    failures: list[str] = []
    unresolved: list[str] = []
    for name, decls in sorted(palettes.items()):
        for ink_token, surface_token, floor in pairs:
            ink = _resolve(decls, ink_token, dark)
            surface = _resolve(decls, surface_token, dark)
            if ink is None or surface is None:
                unresolved.append(f"{name}: {ink_token} on {surface_token}")
                continue
            got = _contrast(ink, surface)
            if got < floor:
                failures.append(
                    f"{name}: {ink_token} ({ink}) on {surface_token} ({surface}) "
                    f"is {got:.2f}:1, needs {floor}:1"
                )

    # An unresolvable token means the evaluator above has fallen behind the
    # stylesheet — a new `color-mix` form, or a renamed token. That must fail
    # loudly: silently skipping pairs is how this test would keep passing while
    # checking less and less.
    assert not unresolved, "tokens this test could not resolve:\n  " + "\n  ".join(unresolved)
    assert not failures, "unreadable colour pairs:\n  " + "\n  ".join(failures)


def test_the_palette_set_agrees_across_every_place_that_names_it():
    """A palette exists in the stylesheet, client, server, HTML and database.

    Five places, because each needs it for a different reason: the stylesheet
    defines it, the client falls back to a known name, the server refuses
    anything else, the settings pane offers it, and the CHECK constraint backs
    the server up. They cannot be collapsed into one — the stylesheet is not
    importable by Python and the server is not readable by CSS — so the thing
    to protect is that they say the same set. A theme added to four of the five
    is a button that 400s, or a theme nobody can reach.
    """

    from pathlib import Path

    root = Path(__file__).parent.parent.parent
    css = (root / "neuman-client" / "web" / "src" / "palette.css").read_text()
    markup = (root / "neuman-client" / "web" / "src" / "index.html").read_text()
    script = (root / "neuman-client" / "web" / "src" / "neuman.js").read_text()
    store = (
        root
        / "neuman-server"
        / "src"
        / "neuman_server"
        / "adapters"
        / "persistence"
        / "identity.py"
    ).read_text()
    migrations = sorted(
        (
            root
            / "neuman-server"
            / "src"
            / "neuman_server"
            / "foundation"
            / "db"
            / "migrations"
        ).glob("*.sql")
    )

    from_css = set(_palette_blocks(css))

    allowlist = re.search(r"PALETTES = \(([^)]*)\)", store)
    assert allowlist, "the store has no PALETTES allowlist"
    from_store = set(re.findall(r'"([^"]+)"', allowlist.group(1)))

    buttons = set(re.findall(r'<button[^>]*data-palette="([^"]+)"', markup))

    client_list = re.search(r"const PALETTES = \[([^\]]*)\]", script)
    assert client_list, "the client has no PALETTES list"
    from_client = set(re.findall(r'"([^"]+)"', client_list.group(1)))

    assert from_css == from_store == buttons == from_client, (
        f"palette sets disagree — stylesheet {sorted(from_css)}, "
        f"server {sorted(from_store)}, buttons {sorted(buttons)}, "
        f"client {sorted(from_client)}"
    )

    # And the database constraint, which is the backstop under the server's
    # allowlist. A palette the CHECK rejects is a 500 waiting for whoever adds
    # a theme and forgets the migration. Later migrations replace the
    # constraint, so take the last file that both names it and defines it — a
    # file that only mentions the name must not shadow the definition.
    check = ""
    for path in migrations:
        text = path.read_text()
        defines = re.search(r"CHECK\s*\(\s*palette\s+IN\s*\(", text)
        if "accounts_palette_known" in text and defines:
            check = text
    assert check, "no migration constrains the palette column"
    in_list = re.search(r"CHECK\s*\(\s*palette\s+IN\s*\(([^)]*)\)", check)
    assert in_list, "the palette CHECK is not an IN list this test can read"
    # The names are family-variant pairs, so hyphens are part of the value.
    from_sql = set(re.findall(r"'([a-z][a-z-]*)'", in_list.group(1)))
    assert from_sql == from_css, (
        f"the CHECK constraint allows {sorted(from_sql)} but the stylesheet "
        f"defines {sorted(from_css)}"
    )


def test_each_document_loads_the_palette_and_only_its_own_layout():
    """Two documents, one palette, and no stylesheet shared between them.

    This is the shape N158 fixed. `index.html` and `admin.html` are different
    views over the same tokens, and the administration page used to load the
    chat window's `neuman.css` to get them — so the admin page's `#sessions`
    host became the chat window's session rail, a fixed column pinned over the
    left edge of the page, and its `#models` card was positioned above the
    viewport. Neither document may name the other's stylesheet: everything past
    `palette.css` has to be the page's own layout, or it is a rule from the
    other document arriving by accident. The chat window's own is two sheets,
    because the task view inside it has its own (D99).
    """

    links = {}
    for page in ("index.html", "admin.html"):
        markup = (WEB / page).read_text()
        links[page] = re.findall(r'<link rel="stylesheet" href="[^"]*?([^"/]+\.css)"', markup)

    assert links["index.html"] == ["palette.css", "neuman.css", "tasks.css"], links[
        "index.html"
    ]
    assert links["admin.html"] == ["palette.css", "admin.css"], links["admin.html"]


def test_the_administration_page_wears_the_accounts_theme():
    """The theme belongs to the account, so it follows the account across documents.

    Both documents select on `:root[data-palette]`, and the identity carries the
    value (D53). `start` is the one path from either identity response into this
    page, so a sign-in and a reload land on the same colours.
    """

    code = (WEB / "admin.js").read_text()
    start = re.search(r"function start\(me\)\s*\{(.*?)\n\}", code, re.S)
    assert start, "the administration page has no `start` to read"
    assert "documentElement.dataset.palette = me.interface.palette" in start.group(1), (
        "the administration page ignores the account's theme"
    )


def test_the_administration_page_borrows_no_token_from_the_chat_window():
    """`admin.css` may use what `palette.css` declares, and nothing else.

    The page's whole dependency on the shared file is the token list. A token
    declared in `neuman.css` instead — the rail's strip, a hover surface — is
    not an error in CSS: an undefined `var()` falls back to the property's
    initial value, so the control renders with no colour at all and nothing
    fails. Reading the value out of the chat window's sheet is how the admin
    page came to load it in the first place (N158).
    """

    palette = (WEB / "palette.css").read_text()
    declared = set(re.findall(r"(--[\w-]+)\s*:", palette))
    used = set(re.findall(r"var\((--[\w-]+)\)", (WEB / "admin.css").read_text()))

    assert used, "admin.css uses no tokens; this test would pass vacuously"
    assert used <= declared, (
        f"tokens used by admin.css that palette.css does not declare: {sorted(used - declared)}"
    )


def test_the_admin_session_log_opens_under_the_row_it_reads():
    """The log appears where the press was, not at the foot of the table.

    The read action used to fill one `<pre>` below the table, so on a list
    longer than the window the click looked like it had done nothing: the answer
    was under the scroll, away from the session it described. The log is a row
    of the table now, inserted directly after the row that asked for it, and the
    same press takes it away again. The rule lives in one helper because the
    usage roster opens its trend the same way.
    """

    script = (WEB / "admin.js").read_text()

    match = re.search(r"async function loadSessions\(\)\s*\{(.*?)\n\}", script, re.S)
    assert match, "loadSessions() is gone; this test would pass vacuously"
    body = match.group(1)
    assert "expandingTable(" in body, "the session log does not open as a detail row"
    assert "toggleRow(row, build)" in script, "a row no longer opens a detail under itself"

    detail = re.search(r"async function toggleRow\(row, build\)\s*\{(.*?)\n\}", script, re.S)
    assert detail, "toggleRow() is gone; the assertions below would pass vacuously"
    inner = detail.group(1)
    assert "row.after(detail)" in inner, "the detail is not inserted under its row"
    assert 'classList.contains("detail")' in inner, (
        "a second press on the same row does not close its detail"
    )


def test_an_expanding_row_is_the_control():
    """A session and a usage row open from the row, and the keyboard reaches both.

    The rows replaced `read` and `open` buttons in an Actions column. A row that
    only answers a mouse is unreachable from the keyboard, so the first cell
    stays a real button carrying the expanded state, and a press on it bubbles to
    the one handler on the row. A second press during a fetch must not open a
    second detail, and the account column names a person rather than a key.
    """

    script = (WEB / "admin.js").read_text()
    css = (WEB / "admin.css").read_text()
    markup = (WEB / "admin.html").read_text()

    helper = re.search(r"function expandable\(row, toggle, build\)\s*\{(.*?)\n\}", script, re.S)
    assert helper, "expandable() is gone; this test would pass vacuously"
    assert "row.onclick" in helper.group(1), "the row does not open its detail"
    assert "if (opening) return" in helper.group(1), "a double press opens two details"
    assert 'setAttribute("aria-expanded"' in helper.group(1), "the state is not announced"

    assert 'element("button", "admin-row-toggle"' in script, "no keyboard target"
    assert "session.account," in script, "the account column does not show the name"
    assert "toggleDetail" not in script, "the per-row button is back"
    assert ".admin-expanding tbody tr:not(.detail):hover" in css, "a row has no hover"

    # All three lists, so no table goes back to a button in an Actions column.
    for fn in ("loadSessions", "loadUsage"):
        body = re.search(rf"async function {fn}\(\)\s*\{{(.*?)\n\}}", script, re.S)
        assert body, f"{fn}() is gone; this test would pass vacuously"
        assert "expandingTable(" in body.group(1), f"{fn} does not expand its rows"
        assert '"Actions"' not in body.group(1), f"{fn} has an Actions column again"
    accounts = re.search(r"function renderAccountSessions\(\)\s*\{(.*?)\n\}", script, re.S)
    assert accounts and "expandable(row, toggle" in accounts.group(1)
    assert "<th>Tokens</th><th>Actions</th>" not in markup, "the Actions column is back"


def test_an_account_opens_from_its_row():
    """The roster has no `open` button, and the keyboard can still reach an account.

    The name is the keyboard's target and carries no handler of its own: a press
    on it bubbles to the row, so a stopped event cannot split the two paths.
    """

    script = (WEB / "admin.js").read_text()
    markup = (WEB / "admin.html").read_text()

    assert "row.onclick = () => openAccount(account)" in script, "a row does not open"
    assert '"admin-row-toggle admin-account-name"' in script, "no keyboard target"
    assert 'element("button", null, "open")' not in script, "the `open` button is back"
    assert "stopPropagation" not in script, "a press no longer reaches the row"

    head = re.search(r'id="account-table">.*?</thead>', markup, re.S)
    assert head, "the roster's header is gone; this test would pass vacuously"
    assert len(re.findall(r"<th[ >]", head.group(0))) == 6, "a column has no cells under it"
    assert "cell.colSpan = 6" in script, "the empty row does not span the table"


def test_an_accounts_actions_sit_behind_one_manage_control():
    """Three rarely used actions, one of them a lockout, do not lead the page.

    They were three equal buttons in the header. Closing has to work every way a
    person leaves a list — a click away, Escape, and Tab — and the state a screen
    reader hears has to move with it.
    """

    script = (WEB / "admin.js").read_text()
    markup = (WEB / "admin.html").read_text()
    css = (WEB / "admin.css").read_text()

    menu = re.search(r'<div id="account-menu".*?\n\s*</div>', markup, re.S)
    assert menu, "the account menu is gone; this test would pass vacuously"
    for action in ("account-reset", "account-admin", "account-disable"):
        assert f'id="{action}"' in menu.group(0), f"{action} is outside the menu"
    assert 'aria-expanded="false"' in markup, "the trigger does not announce its state"

    assert 'addEventListener("focusout"' in script, "tabbing away leaves the menu open"
    assert '$("account-menu").onclick = closeAccountMenu' in script, "an item leaves it open"
    escape = re.search(
        r'if \(event\.defaultPrevented \|\| event\.key !== "Escape"\) return;\s*closeAccountMenu\(\)',
        script,
    )
    assert escape, "Escape does not close the menu"

    # Red belongs to shutting someone out, and not to letting them back in.
    assert 'classList.toggle("bad", !account.disabled)' in script
    assert "body.admin .admin-menu button.bad" in css, "the page's button rule outranks red"


def test_the_admin_rail_folds_from_the_chat_windows_grip():
    """One shape for one act: a circle at mid height, straddling the rail's edge.

    The rail is sticky, so it is a stacking context and the grip's own `z-index`
    means nothing outside it. The rail has to sit above the sticky page head, or
    the head paints over the half of the circle that overhangs the edge.
    """

    markup = (WEB / "admin.html").read_text()
    css = (WEB / "admin.css").read_text()
    script = (WEB / "admin.js").read_text()

    rail = re.search(r'<aside class="admin-rail".*?</aside>', markup, re.S)
    assert rail, "the rail is gone; this test would pass vacuously"
    assert 'id="rail-toggle"' in rail.group(0), "the grip is outside the rail"
    assert 'aria-expanded="true"' in rail.group(0), "the grip does not announce its state"
    assert 'setAttribute("aria-expanded", String(compact))' in script

    grip = re.search(r"body\.admin button\.admin-rail-toggle \{(.*?)\}", css, re.S)
    assert grip, "the grip has no rule; this test would pass vacuously"
    for declaration in ("top: 50%", "right: -14px", "width: 28px", "border-radius: 50%"):
        assert declaration in grip.group(1), f"the grip lost `{declaration}`"

    def layer(selector):
        rule = re.search(rf"^{re.escape(selector)} \{{(.*?)^\}}", css, re.S | re.M)
        assert rule, f"{selector} has no rule; this test would pass vacuously"
        found = re.search(r"z-index:\s*(\d+)", rule.group(1))
        assert found, f"{selector} has no z-index"
        return int(found.group(1))

    assert layer(".admin-rail") > layer(".admin-page-head"), "the page head covers the grip"


def test_an_override_is_edited_on_purpose_and_can_be_abandoned():
    """Cancel restores the saved policy rather than only hiding the controls.

    The form would otherwise keep the abandoned edits, and the read-only view
    would show them as if in force.
    """

    script = (WEB / "admin.js").read_text()
    markup = (WEB / "admin.html").read_text()

    editing = re.search(r"function setEditing\(.*?\n\}", script, re.S)
    assert editing, "setEditing() is gone; this test would pass vacuously"
    assert "`${section}-cancel`" in editing.group(0), "Cancel does not follow the mode"

    cancel = re.search(r"function cancelEditing\(section\)\s*\{(.*?)\n\}", script, re.S)
    assert cancel, "cancelEditing() is gone; this test would pass vacuously"
    assert "resetAccountForm()" in cancel.group(1), "Cancel keeps the abandoned edits"
    # Leaving a tab is a cancel, or the shared form carries the edits to the next.
    tabs = re.search(r"function showAccountTab\(name\)\s*\{(.*?)\n\}", script, re.S)
    assert tabs, "showAccountTab() is gone; this test would pass vacuously"
    assert "cancelEditing(section)" in tabs.group(1), "a tab switch keeps abandoned edits"

    for section in ("policy", "tools", "limits"):
        assert f'id="{section}-cancel"' in markup, f"{section} has no Cancel"
        assert f'$("{section}-cancel").onclick' in script, f"{section}'s Cancel does nothing"


def test_the_admin_session_log_opens_at_its_tail():
    """A failed turn records why in its last events, not its first fifty.

    `?view=events&limit=50` reads from the start. A session with 574,062 events
    — one did — opens on its build line and its opening message, and the
    `model_error` at the end is unreachable from the page. The API already
    answers `last`, which `window` resolves in SQL, so the same page asked for
    from the other end is the fix.
    """

    script = (WEB / "admin.js").read_text()

    assert "view=events&last=50" in script, (
        "the session log opens at the head, so a failed turn's last events are not shown"
    )
    assert "view=events&limit=50" not in script, (
        "a head-limited read is still there; the detail can still open at the wrong end"
    )


def test_the_admin_session_log_wraps_instead_of_widening_the_page():
    """Nothing a session contains may set the width of the table it sits in.

    In table auto layout a cell's minimum width is its content's longest
    unbreakable run, so a base64 tool payload — 1814px measured in a 953px
    window — stretched the table, the card and the page past the viewport. The
    answer is that both the log and the cells break words: `pre-wrap` on the
    box keeps JSON's indentation and ends the line at the edge, and `anywhere`
    on the cells is what changes the *intrinsic* size the layout algorithm
    reads. `break-word` would not do — it breaks the line and leaves the
    minimum where it was.

    Pinned rather than `table-layout: fixed`, which is the answer this page
    tried first: fixed layout needs column widths, so the narrow columns were
    pinned in pixels, and at 360px those pixels were wider than the window and
    the page scrolled sideways again (N159).
    """

    styles = (WEB / "admin.css").read_text()

    pre = re.search(r"(?m)^\.admin-pre \{([^}]*)\}", styles)
    assert pre, "the log box rule is gone"
    assert "white-space: pre-wrap" in pre.group(1), (
        "the log prints one long line and pushes the table wider than the window"
    )
    assert "overflow-wrap: anywhere" in pre.group(1), (
        "a payload with no space in it still has a minimum width"
    )

    # Every `.admin-table td` rule, because the padding and the wrapping are
    # declared separately and the first one to match would be the wrong one.
    cells = re.findall(r"(?m)^\.admin-table td\b[^{]*\{([^}]*)\}", styles)
    assert cells, "the table cells rule is gone"
    assert any("overflow-wrap: anywhere" in block for block in cells), (
        "a long title or id sets the table's floor and the page scrolls sideways"
    )


def test_the_interface_preference_is_labelled_theme():
    """The dialog says Theme; the mechanism keeps the token set's own name.

    A theme is still a palette in the code — `data-palette`, `PALETTES`, the
    column — because that is what the stylesheet defines. The label is what a
    person picks, and it is the word that must not drift back.
    """

    markup = (WEB / "index.html").read_text()
    assert '<span class="pref-name">Theme</span>' in markup
    assert 'aria-label="Theme"' in markup
    assert "Colour palette" not in markup
    for label in ("Everforest Dark", "Everforest Light", "Gruvbox Dark", "Gruvbox Light"):
        assert label in markup, f"the picker does not offer {label}"


def test_the_instruction_limit_agrees_across_every_place_that_names_it():
    """One number, four readers: the store, the markup, the client, the column.

    The client counts characters beside the box, `maxlength` stops a paste, the
    store refuses an over-long value and the CHECK is the backstop under it.
    They cannot be collapsed into one — the browser is not importable by Python
    — so the thing to protect is that they say the same number. A cap raised in
    three places is a box that accepts text the server 400s; one lowered in the
    migration alone is a write that fails as a constraint violation.
    """

    root = Path(__file__).resolve().parent.parent.parent
    markup = (WEB / "index.html").read_text()
    script = (WEB / "neuman.js").read_text()
    store = (
        root
        / "neuman-server"
        / "src"
        / "neuman_server"
        / "adapters"
        / "persistence"
        / "identity.py"
    ).read_text()
    migrations = sorted(
        (
            root
            / "neuman-server"
            / "src"
            / "neuman_server"
            / "foundation"
            / "db"
            / "migrations"
        ).glob("*.sql")
    )

    from_store = re.search(r"MAX_INSTRUCTIONS = (\d+)", store)
    assert from_store, "the store has no MAX_INSTRUCTIONS"

    from_markup = re.search(r'id="pref-instructions"[^>]*maxlength="(\d+)"', markup, re.S)
    assert from_markup, "the instructions box has no maxlength"

    from_client = re.search(r"MAX_INSTRUCTIONS = (\d+)", script)
    assert from_client, "the client has no MAX_INSTRUCTIONS"

    # The last migration that both names the constraint and defines it: a file
    # that merely mentions the name must not shadow the definition, the same
    # rule the palette test follows.
    check = ""
    for path in migrations:
        text = path.read_text()
        if "accounts_instructions_bounded" in text and "char_length(instructions)" in text:
            check = text
    assert check, "no migration bounds the instructions column"
    from_sql = re.search(r"char_length\(instructions\) <= (\d+)", check)
    assert from_sql, "the instructions CHECK is not a comparison this test can read"

    numbers = {
        from_store.group(1),
        from_markup.group(1),
        from_client.group(1),
        from_sql.group(1),
    }
    assert len(numbers) == 1, f"instruction limits disagree: {sorted(numbers)}"


def test_the_assistant_pane_rides_with_the_identity_and_saves_on_a_press():
    """Two identity paths, one pane, and a write the person asks for.

    The field rides on `/api/login` and `/api/me`; a client that read it from
    one and not the other gives instructions that apply on sign-in and vanish on
    reload. And a textarea is saved on a press rather than per keystroke, because a request
    per character is not a preference write.

    Every caller hands `applyInstructions` a *payload* and the field is read
    inside it. That is asserted rather than assumed: the first version took the
    text, the save passed its reply object, and a successful write cleared the
    box it had just stored (N156).
    """

    markup = (WEB / "index.html").read_text()
    script = (WEB / "neuman.js").read_text()

    assert 'id="tab-assistant"' in markup
    assert 'id="pane-assistant"' in markup
    assert 'id="pref-instructions"' in markup

    tabs = re.search(r"const SETTINGS_TABS = \[([^\]]*)\]", script)
    assert tabs and '"assistant"' in tabs.group(1), "the Assistant tab is not selectable"

    # The field is read in one place, from the payload every caller has.
    assert "payload.instructions" in script, (
        "applyInstructions no longer reads the field from its argument, so the "
        "save can hand it the wrong thing again"
    )
    assert "applyInstructions(body)" in script
    assert "applyInstructions(me)" in script
    # The save path passes the reply itself, which is what went wrong before:
    # `applyInstructions(await api(...))` is right only because the function
    # reads the field, and this is the pair that must stay in step.
    assert re.search(r"applyInstructions\(await api\(", script), (
        "the save no longer hands the reply to applyInstructions"
    )

    assert 'api("PATCH", "/api/me/instructions"' in script
    assert '$("pref-instructions-save").onclick = saveInstructions;' in script


def test_the_working_set_window_is_a_setting_the_account_keeps():
    """How long a session sticks around is the person's, and it applies at once.

    A preference of the account rather than of the browser, for D53's reason:
    it is about how this person works, so it follows them to another machine.
    What it acts on — the views a window holds — stays per window, which is the
    one part of the working set that was never the server's business.

    The control is a number with a unit rather than a switch, and it writes on
    change: half-typed numbers are not settings, and 0 is a real answer rather
    than the absence of one.
    """

    markup = (WEB / "index.html").read_text()
    script = (WEB / "neuman.js").read_text()

    assert 'id="tab-sessions"' in markup, "the Sessions tab is not in the dialog"
    assert 'id="pane-sessions"' in markup, "the Sessions pane is not in the dialog"
    assert 'id="pref-active-minutes"' in markup, "there is no control for the window"

    tabs = re.search(r"const SETTINGS_TABS = \[([^\]]*)\]", script)
    assert tabs and '"sessions"' in tabs.group(1), "the Sessions tab is not selectable"

    # Painted from the state, written through the same partial PATCH the other
    # identity preferences use, and clamped to the bound before it is sent.
    assert '$("pref-active-minutes").value = String(interfacePrefs.active_minutes);' in script
    assert "setInterface({ active_minutes: minutes })" in script
    assert 'api("PATCH", "/api/me/interface", change)' in script
    # Sliced rather than regexed: the pattern would have to start with `$(`, and
    # this interpreter's `re` does not match an escaped dollar followed by an
    # unescaped group open (`\$("x")` fails where `\$\("x"\)` matches), which
    # made the first version of this assertion fail against the right source.
    start = script.find('"pref-active-minutes").onchange')
    assert start >= 0, "the window control has no writer"
    handler = script[start : script.find("\n};", start)]
    assert "Math.min(MAX_ACTIVE_MINUTES" in handler, (
        "a typed number is sent unclamped, so the route refuses it"
    )
    assert "p.active_minutes" in script, (
        "the window is not read from the identity payload, so it reverts on reload"
    )


def test_the_working_set_bound_is_one_number_everywhere():
    """The client clamps, the route refuses, and the column backs both up.

    Four places hold the same ceiling for four different reasons — the field's
    `max`, the client's clamp, the store's allowlist the route reports when it
    refuses, and the CHECK constraint — and a test that pins them together is
    what makes a change to one of them a visible disagreement rather than a
    value the client sends and the database rejects.
    """

    from pathlib import Path

    root = Path(__file__).parent.parent.parent
    markup = (root / "neuman-client" / "web" / "src" / "index.html").read_text()
    script = (root / "neuman-client" / "web" / "src" / "neuman.js").read_text()
    store = (
        root
        / "neuman-server"
        / "src"
        / "neuman_server"
        / "adapters"
        / "persistence"
        / "identity.py"
    ).read_text()
    migrations = sorted(
        (
            root
            / "neuman-server"
            / "src"
            / "neuman_server"
            / "foundation"
            / "db"
            / "migrations"
        ).glob("*.sql")
    )

    from_markup = re.search(r'id="pref-active-minutes"[^>]*max="(\d+)"', markup, re.S)
    assert from_markup, "the window field has no maximum"

    from_client = re.search(r"MAX_ACTIVE_MINUTES = (\d+)", script)
    assert from_client, "the client has no MAX_ACTIVE_MINUTES"

    from_store = re.search(r"ACTIVE_MINUTES_MAX = (\d+)", store)
    assert from_store, "the store has no ACTIVE_MINUTES_MAX"

    # The last migration that both names the constraint and defines it, the rule
    # the palette and instruction bounds follow.
    check = ""
    for path in migrations:
        text = path.read_text()
        if "accounts_active_minutes_bounded" in text and "active_minutes BETWEEN" in text:
            check = text
    assert check, "no migration bounds the active window"
    from_sql = re.search(r"active_minutes BETWEEN \d+ AND (\d+)", check)
    assert from_sql, "the window CHECK is not a range this test can read"

    numbers = {
        from_markup.group(1),
        from_client.group(1),
        from_store.group(1),
        from_sql.group(1),
    }
    assert len(numbers) == 1, f"the working-set bounds disagree: {sorted(numbers)}"


def _palette_blocks(css: str) -> dict[str, dict[str, str]]:
    """Every palette's token declarations, keyed by palette name.

    Parses the `:root…{}` blocks rather than importing a list from
    anywhere: the stylesheet is the source of truth for what palettes exist (a
    palette *is* a `:root[data-palette="…"]` block), so a theme added there is
    picked up by these tests without a second registration to forget.
    """

    blocks: dict[str, dict[str, str]] = {}
    for selector, body in re.findall(r"\n  (:root[^{]*)\{([^{}]*)\}", css):
        # Keyed on `:root[data-palette=` specifically, not on the substring
        # "data-palette" anywhere in the selector: dark's rule names
        # `.swatch[data-palette="dark"]` alongside a bare `:root`, so a looser
        # check sends it down the wrong branch and silently gives dark no
        # tokens at all — which made every pair unresolvable rather than wrong.
        names = re.findall(r':root\[data-palette="([^"]+)"\]', selector)
        if not names:
            names = ["dark"]
        for name in names:
            decls = blocks.setdefault(name, {})
            for prop, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body):
                decls[prop] = value.strip()
    return blocks


def _resolve(decls: dict[str, str], token: str, css_dark: dict[str, str]) -> str | None:
    """Flatten one token to a hex string, following `color-mix` and `var()`.

    A small evaluator rather than a browser, and deliberately narrow: it handles
    the two forms this stylesheet actually uses to build a colour from other
    colours. Anything else returns None and is skipped rather than guessed at,
    because a contrast number derived from a misparsed value is worse than no
    number — it would pass while describing a colour nothing renders.
    """

    def lookup(name: str) -> str | None:
        return decls.get(name, css_dark.get(name))

    def evaluate(value: str, depth: int = 0) -> str | None:
        if depth > 8:
            return None
        value = value.strip()

        hexmatch = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value)
        if hexmatch:
            digits = hexmatch.group(1)
            if len(digits) == 3:
                digits = "".join(c * 2 for c in digits)
            return "#" + digits.lower()

        varmatch = re.fullmatch(r"var\((--[\w-]+)\)", value)
        if varmatch:
            inner = lookup(varmatch.group(1))
            return evaluate(inner, depth + 1) if inner else None

        # `color-mix(in srgb, A p%, B)` and `color-mix(in srgb, A p%, B q%)`,
        # which is every form in this file.
        mixmatch = re.fullmatch(r"color-mix\(in srgb,\s*(.+)\)", value, re.S)
        if mixmatch:
            parts = [p.strip() for p in _split_top(mixmatch.group(1))]
            if len(parts) != 2:
                return None
            colours: list[str] = []
            weights: list[float | None] = []
            for part in parts:
                pct = re.search(r"(?:calc\([^)]*\)|[\d.]+%)\s*$", part)
                weight: float | None = None
                if pct:
                    text = pct.group()
                    part = part[: pct.start()].strip()
                    # `calc(100% - var(--x))` appears as the complement of a
                    # percentage token; resolve it the same way.
                    calc = re.fullmatch(r"calc\(100%\s*-\s*var\((--[\w-]+)\)\)", text)
                    if calc:
                        raw = lookup(calc.group(1))
                        if raw is None or not raw.endswith("%"):
                            return None
                        weight = 100.0 - float(raw.rstrip("%"))
                    elif text.endswith("%"):
                        weight = float(text.rstrip("%"))
                    else:
                        return None
                elif part.startswith("var(") and part.count("var(") == 1:
                    # A bare `var(--pct)` used as the weight, e.g. `var(--tint)`.
                    pass
                resolved_weight = weight
                # A weight written as a token: `var(--accent) var(--tint)`.
                tokenised = re.fullmatch(r"(.+?)\s+var\((--[\w-]+)\)", part)
                if resolved_weight is None and tokenised:
                    raw = lookup(tokenised.group(2))
                    if raw is None or not raw.endswith("%"):
                        return None
                    resolved_weight = float(raw.rstrip("%"))
                    part = tokenised.group(1).strip()
                colour = evaluate(part, depth + 1)
                if colour is None:
                    return None
                colours.append(colour)
                weights.append(resolved_weight)

            if weights[0] is None and weights[1] is None:
                weights = [50.0, 50.0]
            elif weights[0] is None:
                weights[0] = 100.0 - (weights[1] or 0.0)
            elif weights[1] is None:
                weights[1] = 100.0 - weights[0]

            total = (weights[0] or 0.0) + (weights[1] or 0.0)
            if total <= 0:
                return None
            share = (weights[0] or 0.0) / total
            out = []
            for i in (1, 3, 5):
                a = int(colours[0][i : i + 2], 16)
                b = int(colours[1][i : i + 2], 16)
                out.append(round(a * share + b * (1 - share)))
            return "#{:02x}{:02x}{:02x}".format(*out)

        return None

    raw = lookup(token)
    return evaluate(raw) if raw else None


def _split_top(text: str) -> list[str]:
    """Split on commas that are not inside parentheses."""

    parts, depth, current = [], 0, ""
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return parts


def _contrast(a: str, b: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def luminance(colour: str) -> float:
        r, g, bl = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(bl)

    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_the_environment_outline_follows_the_probe_and_not_the_path():
    """A directory being set is not the same fact as its environment being usable.

    These are two axes and the outline must track the second. Drawing it from the
    path would make every chosen directory look like a working environment,
    which is a status light reporting its own input.

    `showWorkingDir` stays the single writer: it drops what was known about the
    old directory before anything is drawn, then re-probes. Painting the previous
    answer against a new path is the one wrong state available here.
    """

    script = (WEB / "neuman.js").read_text()

    found = re.search(r"function showWorkingDir\(value\)\s*\{(.*?)\n\}", script, re.S)
    assert found, "showWorkingDir is gone; this test would pass vacuously"
    body = found.group(1)

    cleared = body.index("currentEnvironment = null")
    painted = body.index("paintEnvironment()")
    probed = body.index("probeEnvironment(")
    assert cleared < painted < probed, (
        "the outline must be cleared, drawn, then re-probed; any other order "
        "paints one directory's environment against another's path"
    )

    paint = re.search(r"function paintEnvironment\(\)\s*\{(.*?)\n\}", script, re.S)
    assert paint, "paintEnvironment is gone; this test would pass vacuously"
    assert "currentEnvironment" in paint.group(1), (
        "the outline is drawn from something other than the probe's answer"
    )


def test_the_environment_state_is_not_persisted_with_the_session():
    """It is a fact about the user's disk now, not about the conversation.

    A `nix-collect-garbage` turns a resolved environment into `env_collected`
    without the working directory moving, so a value stored on the session would
    be a status light that lies. It is re-probed on every change instead, which
    is why no schema field was added for it.
    """

    script = (WEB / "neuman.js").read_text()

    assert 'api("PATCH"' in script, "no session PATCH found; this test is vacuous"

    # What the client assigns into a PATCH body is the whole of what it stores on
    # a session. Asserted on those assignments rather than on the file, because
    # the word "environment" appears throughout the state table and a substring
    # search over the source reported a persisted field that does not exist.
    assigned = set(re.findall(r"body\.(\w+)\s*=", script))
    assert assigned, "no PATCH body fields found; this test would pass vacuously"
    for field in assigned:
        assert "env" not in field.lower(), (
            f"body.{field} would persist a fact that goes stale the moment "
            "a garbage collection runs"
        )


def test_a_stale_environment_probe_is_dropped():
    """Choosing two directories quickly must not colour the second from the first.

    The probe is a dispatch to the user's machine, so a cold directory takes long
    enough for answers to arrive out of order. Without a generation guard the
    first reply lands last and the outline describes a directory nobody is in.
    """

    script = (WEB / "neuman.js").read_text()

    found = re.search(r"async function probeEnvironment\([^)]*\)\s*\{(.*?)\n\}", script, re.S)
    assert found, "probeEnvironment is gone; this test would pass vacuously"
    body = found.group(1)

    assert "++environmentProbe" in body, "the probe takes no generation"
    assert "generation !== environmentProbe" in body, (
        "a reply from a superseded probe is not dropped"
    )
    assert "currentWorkingDir" in body, (
        "an answer is not checked against the directory it is about"
    )


def test_a_failed_environment_probe_is_silent():
    """An unprobed directory and one with no environment look the same, and should.

    Neither is a state to act on. A chooser that reported its own plumbing would
    be noise in the one place the user is reading a path, and the probe is
    additive — its failure must cost the control nothing.
    """

    script = (WEB / "neuman.js").read_text()

    found = re.search(r"async function probeEnvironment\([^)]*\)\s*\{(.*?)\n\}", script, re.S)
    assert found, "probeEnvironment is gone; this test would pass vacuously"
    body = found.group(1)

    assert "catch" in body, "a failed probe is not caught"
    assert "note(" not in body, "a probe failure is not worth a line in the transcript"


def test_the_environment_state_is_not_carried_by_colour_alone():
    """A coloured border alone is unreadable to many of the people reading it.

    So the same fact goes in the title and the accessible name, where
    `showWorkingDir` already writes both. The CSS is the decoration; the text is
    the statement.
    """

    script = (WEB / "neuman.js").read_text()

    paint = re.search(r"function paintEnvironment\(\)\s*\{(.*?)\n\}", script, re.S)
    assert paint, "paintEnvironment is gone; this test would pass vacuously"
    body = paint.group(1)

    assert ".title" in body, "the state is not in the tooltip"
    # The call, not the substring: `setAttribute("aria-label2", ...)` contains
    # "aria-label" and sets nothing a screen reader reads.
    assert re.search(r'setAttribute\(\s*"aria-label"', body), (
        "the state is not in the accessible name"
    )
    # And the state has to be what is written there, not just the path.
    accessible = re.search(r'setAttribute\(\s*"aria-label"(.*?)\);', body, re.S)
    assert accessible and "state" in accessible.group(1), (
        "the accessible name does not carry the environment state"
    )

    states = re.search(r"const ENVIRONMENT_STATES = \{(.*?)\n\};", script, re.S)
    assert states, "ENVIRONMENT_STATES is gone; this test would pass vacuously"
    entries = re.findall(r"text: \"([^\"]*)\"", states.group(1))
    assert entries, "no state carries words"
    for text in entries:
        assert text.strip(), "a state has a colour and no words"


def test_a_directory_with_no_environment_draws_no_outline():
    """Most directories are not projects, and an outline on all of them says nothing.

    `no_environment` is the ordinary condition of a folder holding the data a
    script is pointed at (N85). A folder of DICOM files is not in a degraded
    state for failing to be a flake, so it gets no border and no sentence.
    """

    script = (WEB / "neuman.js").read_text()

    states = re.search(r"const ENVIRONMENT_STATES = \{(.*?)\n\};", script, re.S)
    assert states, "ENVIRONMENT_STATES is gone; this test would pass vacuously"

    assert "no_environment" not in states.group(1), (
        "a directory that is not a project must map to no state at all"
    )
    assert "ok:" in states.group(1), "the resolved state is not drawn"


def test_the_environment_outline_adds_no_sixth_hue():
    """The palette documents five hues and says a sixth would need a sixth meaning.

    There isn't one here: a usable environment is a success (`--ok`), the rebuild
    states are "careful, and reversible" (`--warn`), and a cache nix did not
    write is a refusal (`--bad`). Asserted because the tempting fix for "these
    two ambers look alike" is a new colour variable.
    """

    css = (WEB / "neuman.css").read_text()

    rules = re.findall(r"#cwd-bar\.env-\w+[^{]*\{[^}]*\}", css)
    assert rules, "the environment outline is not in the stylesheet"
    joined = "\n".join(rules)

    # `--edge-warn` for the amber one: it is the file's only tinted border and
    # already exists for exactly this, so a second derivation of the same colour
    # would be the drift the palette's one-definition rule prevents.
    for hue in ("--ok", "--edge-warn", "--bad"):
        assert hue in joined, f"{hue} is unused; the three states are not distinct"
    assert not re.search(r"--env-[a-z]+:", css), (
        "a new hue variable for the environment; the palette allows five"
    )
    assert "border-color" in joined, "the state is not drawn as a border"


def test_a_folded_stage_holds_the_tail():
    """A stage folds into a reserved blank, and the reply fills it before the tail moves.

    The card's top must not move when the run ends. The blank is a real element at
    the end of the transcript whose height is the space being held; `settle` keeps
    the invariant `content span + reserve` constant. It has to keep the pin through
    the frame the fold starts on: at that instant the target is exactly zero, and
    clearing it there drops the hold before the card has moved (the mockup's bug).
    The reply bubble is flush, because the column gap is itself a height change the
    reserve has not funded yet. `releaseReserve` closes a blank the reply did not
    fill when the turn ends.
    """

    code = re.sub(r"//.*", "", (WEB / "neuman.js").read_text())
    css = re.sub(r"/\*.*?\*/", "", (WEB / "neuman.css").read_text(), flags=re.S)

    assert "function contentSpan()" in code, "there is no content-span measurement"
    settle = re.search(r"function settle\(\)\s*\{(.*?)\n\}", code, re.S)
    assert settle, "settle is gone; this test would pass vacuously"
    body = settle.group(1)
    assert "pinned - contentSpan()" in body, "the reserve is not held against the content"
    assert "target < -0.5" in body, (
        "the pin is dropped at zero, before the fold has made any space"
    )
    assert "target > 0.5" in body, "the fold does not grow the reserve"

    fold = re.search(r"function foldStageForOutput\(run\)\s*\{(.*?)\n\}", code, re.S)
    assert fold, "foldStageForOutput is gone; this test would pass vacuously"
    body = fold.group(1)
    assert "pinBottom()" in body, "the fold does not pin the bottom"
    assert "run._live = false" in body, "the run stays live after its stage folds"
    assert "stage.remove()" in body, "the stage is never removed"

    add = re.search(r"function add\(node[^)]*\)\s*\{(.*?)\n\}", code, re.S)
    assert add, "add is gone; this test would pass vacuously"
    body = add.group(1)
    assert "foldStageForOutput(run)" in body, "prose does not fold the run's stage"
    assert 'node.classList.add("flush")' in body, (
        "the reply bubble carries the column gap into the reserved blank"
    )
    assert body.index("foldStageForOutput(run)") < body.index("appendTranscript(node)"), (
        "the bubble is appended before the blank is pinned"
    )

    release = re.search(r"function releaseReserve\(ms\)\s*\{(.*?)\n\}", code, re.S)
    assert release, "releaseReserve is gone; this test would pass vacuously"
    assert "setReserve(0)" in release.group(1), "a leftover blank is never closed"

    assert "#log > .tail-reserve" in css, "the reserve has no layout rule"
    assert "margin-top: -10px" in css, "the reserve adds the column gap to its height"
    assert ".turn.agent.flush { margin-top: -10px; }" in css, (
        "the reply into a blank still carries the column gap"
    )
    assert "overflow-anchor: none" in css, (
        "browser scroll anchoring can move the viewport on an in-place open"
    )

    view = re.search(r"function makeView\(session, branch\)\s*\{(.*?)\n\}", code, re.S)
    assert view, "makeView is gone; this test would pass vacuously"
    assert "reserveEl: reserve" in view.group(1), "a view does not own its reserve"
    assert "reserveEl = view.reserveEl" in code, "a view switch does not load its reserve"
    assert "view.reserveEl = reserveEl" in code, "a view switch does not keep its reserve"


def test_the_overview_leads_with_what_needs_the_operator():
    """What the first tab is for: does anything need me, and what is happening.

    The server decides what needs attention (`/admin/overview`, its tests), so
    the page's part is to draw every item it is given, worst first as given,
    with the tab that fixes it, and to say so plainly when there is nothing. The
    appliance's identity — build, uptime, address, a schema that disagrees — is
    the footer, kept for bug reports.

    Read as source, like the rest of this file: there is no DOM harness, and the
    rule that can go stale is which fields the page reads.
    """

    script = (WEB / "admin.js").read_text()
    styles = (WEB / "admin.css").read_text()

    assert '"/api/admin/overview"' in script, "the overview reads no overview"
    attention = re.search(r"function paintAttention\(items\)\s*\{(.*?)\n\}", script, re.S)
    assert attention, "paintAttention() is gone; this test would pass vacuously"
    body = attention.group(1)
    assert "Nothing needs attention." in body, "a calm appliance says nothing at all"
    assert "showTab(item.tab)" in body, "an item does not lead to the tab that fixes it"

    paint = re.search(r"function paintOverview\(body\)\s*\{(.*?)\n\}", script, re.S)
    assert paint, "paintOverview() is gone; this test would pass vacuously"
    footer = paint.group(1)
    assert "about.schema.applied === about.schema.expected" in footer, (
        "the footer cannot tell a schema that disagrees from one that matches"
    )
    assert "shortBuild(about.build)" in footer and "about.uptime_s" in footer

    # Today is tokens and dollars: dollars alone hide the local share.
    today = re.search(r"function paintToday\(today\)\s*\{(.*?)\n\}", script, re.S)
    assert today and "today.tokens" in today.group(1) and "today.usd" in today.group(1)

    # Warnings use the warning hue and errors the bad one, never the accent:
    # an attention line in the accent colour reads as a normal state.
    rule = re.search(r"(?m)^\.admin-attention li \{([^}]*)\}", styles)
    assert rule and "var(--edge-warn)" in rule.group(1), "a warning has no warning look"
    error = re.search(r"(?m)^\.admin-attention li\.error \{([^}]*)\}", styles)
    assert error and "var(--bad)" in error.group(1), "an error looks like a warning"


def test_the_usage_tab_lists_accounts_rather_than_waiting_for_a_name():
    """The tab that drew nothing until it was asked the right word.

    It read `/admin/usage/{account}` from a text box and returned early while
    the box was empty — no message, and the tab had already been marked loaded,
    so opening it again never retried. The roster is the answer to "who is
    spending today", and an account at zero belongs in it.
    """

    script = (WEB / "admin.js").read_text()

    match = re.search(r"async function loadUsage\(\)\s*\{(.*?)\n\}", script, re.S)
    assert match, "loadUsage() is gone; this test would pass vacuously"
    body = match.group(1)

    assert '"/api/admin/usage"' in body, "the tab still reads one account, not the roster"
    assert "data.accounts" in body, "the response is not drawn as a table"
    assert "data.day" in body, "nothing says which day the figures are from"
    assert "data.days" in body, "nothing says how long the trend is"
    assert "In flight" in body, "reserved tokens are not reported apart from settled"
    assert "tokens_daily" in body, "a capped account is shown without its cap"
    assert "account.history" in body, "the row has no trend to draw"
    assert "spark(account.history.slice(-7), scale)" in body, (
        "the roster's trend is not a week, or is not the row's history"
    )
    # The floor: on a shared scale a small account's day of spend rounds to an
    # empty track, which reads as no activity rather than little.
    spark_fn = re.search(r"function spark\(history, scale\)\s*\{(.*?)\n\}", script, re.S)
    assert spark_fn, "spark() is gone; this test would pass vacuously"
    assert "minHeight" in spark_fn.group(1), (
        "a day of spend is drawn as an empty track beside a larger account"
    )
    assert "account.last_call" in body, "the row does not say when it last spent"
    assert "usage-account" not in script, "the blind account box is back"
    # A total read when the tab was first opened looks current forever otherwise.
    assert 'loaded.delete("usage")' in script, "a second visit reuses the first reading"

    # The card says what the figures are, because a settled total and a held
    # reservation are different claims and the smaller one is not yet a charge.
    assert '"usage-caveat"' in script, "the card does not say what it is counting"
    assert '"usage-summary"' in script, "the totals strip is gone"
    # A row opens its fortnight under itself, where the reader is looking (D64).
    assert "build: () => usageDetail(account)" in body, "a row does not open its detail"


def test_the_admin_sessions_table_says_what_each_conversation_spent():
    """A Tokens column, read from each session's own log by the route.

    The figure was already stored — every `response_final` carries the
    provider's usage — and nothing drew it, so "what did this conversation
    use" had no answer on the panel.
    """

    script = (WEB / "admin.js").read_text()

    match = re.search(r"async function loadSessions\(\)\s*\{(.*?)\n\}", script, re.S)
    assert match, "loadSessions() is gone; this test would pass vacuously"
    body = match.group(1)

    assert "session.tokens" in body, "the table does not read the per-session total"
    assert '"Tokens"' in body, "the column has no header"


def test_the_accounts_page_is_a_paged_search_over_one_request():
    """The roster asks for a page, not the whole appliance.

    The old list fetched every account and then one groups query per row; at a
    few hundred accounts that is a table that pauses before it draws. Search
    and paging are query parameters here, so a name found by search is found
    before the page is cut rather than after it.
    """

    script = (WEB / "admin.js").read_text()

    match = re.search(r"async function refreshAccounts\(\)\s*\{(.*?)\n\}", script, re.S)
    assert match, "refreshAccounts() is gone; this test would pass vacuously"
    body = match.group(1)

    assert "/api/admin/accounts?" in body, "the roster does not ask the server for a page"
    assert "limit:" in body and "offset:" in body, "the request is not paged"
    assert 'params.set("q"' in body, "search is not part of the query"
    assert "create-account" not in script, "the permanent create card is back"


def test_opening_an_account_swaps_the_list_for_a_page_of_its_own():
    """A row opens a view, not an expanding row.

    The account page is a policy editor, a tool matrix, usage and sessions; an
    expansion under the row would push the list off the screen and make "the
    next account" a scroll hunt. The list's search and page survive the swap.
    """

    script = (WEB / "admin.js").read_text()
    page = (WEB / "admin.html").read_text()

    match = re.search(r"async function openAccount\(account\)\s*\{(.*?)\n\}", script, re.S)
    assert match, "openAccount() is gone; this test would pass vacuously"
    body = match.group(1)
    assert 'classList.add("hidden")' in body or '"account-list"' in body, (
        "the list is not hidden when the account opens"
    )
    assert '"account-detail"' in body, "the account page is not shown"

    for tab in ("overview", "policy", "tools", "limits", "usage", "sessions"):
        assert f'data-account-tab="{tab}"' in page, f"the {tab} section has no tab"


def test_a_policy_save_writes_whole_lists_and_keeps_what_it_did_not_touch():
    """An override is the set in force for that field (D87).

    Starting from the overlay as written means a field the operator did not touch
    survives the save, and one moved back to what the roles give stops being an
    override rather than freezing a copy of them.
    """

    script = (WEB / "admin.js").read_text()

    match = re.search(r"function overlayFragment\(\)\s*\{(.*?)\n\}", script, re.S)
    assert match, "overlayFragment() is gone; this test would pass vacuously"
    body = match.group(1)
    assert "clone(loaded.overlay" in body, "an untouched override would be dropped"
    assert "sameList(form[field], inherited[field] || [])" in body, (
        "a list put back as the roles give it stays frozen"
    )
    assert 'api("PUT", `/api/admin/policy/${name}/overlay`' in script
    assert 'api("DELETE", `/api/admin/policy/${name}/overlay`' in script, (
        "clearing the overlay has no call"
    )


def test_a_tool_row_relabels_itself_when_its_override_changes():
    """The row's source answers "why is this on", so it moves with the switch.

    Re-rendering the matrix would lose the focus the operator just used; the
    label is updated in place instead. The smoke case is turning off a tool a
    role grants: the row must say so before the save, not after the next refresh.
    """

    script = (WEB / "admin.js").read_text()
    match = re.search(r"function renderToolMatrix\(\)\s*\{(.*?)\n\}", script, re.S)
    assert match, "renderToolMatrix() is gone; this test would pass vacuously"
    body = match.group(1)
    assert "toolSource(tool, has)" in body
    assert "source.textContent = toolSource(tool, on)" in body
    assert "renderToolMatrix()" not in body, "a switch redraws the list and loses focus"


def test_the_account_page_reads_the_catalog_and_its_own_figures():
    """The matrix needs every tool; the usage and sessions are one account's.

    `/api/tools` is what the signed-in account may call and would hide every
    tool no role granted. `/api/admin/usage` without an account would send every
    other account's days across the socket to draw one chart.
    """

    script = (WEB / "admin.js").read_text()

    assert 'api("GET", "/api/admin/tools")' in script, "the matrix has no catalog"
    assert 'api("GET", `/api/admin/usage?account=${name}&days=${USAGE_DAYS}`)' in script, (
        "the account's usage is not scoped to it"
    )
    assert 'api("GET", `/api/admin/sessions?account=${name}`)' in script, (
        "the account's sessions are not scoped to it"
    )


def test_a_new_account_is_a_dialog_not_a_card_that_never_goes_away():
    """The create form is opened by a button and closed when it succeeds.

    It occupied a third of the Accounts tab permanently, which is a form most
    visits do not need and the reason the list had less room than it needed.
    """

    script = (WEB / "admin.js").read_text()
    page = (WEB / "admin.html").read_text()

    assert 'id="new-account"' in page, "there is no button to open the dialog"
    assert 'id="dialog"' in page, "there is no dialog"
    assert 'id="create-account"' not in page, "the permanent create card is back"
    assert "function newAccountDialog()" in script, "the create flow is not a dialog"
    assert '"Create account"' in script, "the dialog action is not labelled"


def test_the_tabs_are_the_layers_rather_than_one_policy_document():
    """Defaults, roles, users, models, providers, grants and the prompt were
    all sections of one textarea behind one tab. Each layer now has its own
    place, and the raw baseline editor is gone.
    """

    page = (WEB / "admin.html").read_text()
    for tab in (
        "overview",
        "accounts",
        "groups",
        "roles",
        "models",
        "providers",
        "prompt",
        "sessions",
        "usage",
        "audit",
        "events",
    ):
        assert f'data-tab="{tab}"' in page, f"no {tab} tab"
    assert 'data-tab="policy"' not in page, "the Policy tab is back"
    assert 'id="baseline"' not in page, "the raw baseline editor is back"


def test_the_rail_is_the_page_map_and_says_where_you_are():
    """One destination per section, and the current one is in the tree.

    The eleven tabs were a wrapping bar whose active state was a class on one
    button, so a screen reader was told nothing about which section was
    showing (D80). The rail marks it with `aria-current`, which is the attribute
    for "the page you are on". The section is also written into the hash, so a
    reload lands where the operator was — but written with `replaceState`,
    because assigning `location.hash` fires `hashchange` and a second switch
    would fetch the section twice and leave a second overview watcher running.
    """

    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()

    tabs = re.findall(r'data-tab="([a-z]+)"', page)
    sections = re.findall(r'id="tab-([a-z]+)"', page)
    assert tabs, "no tabs found; this test would pass vacuously"
    assert len(tabs) == len(set(tabs)), "a tab is declared twice"
    assert set(tabs) == set(sections), (
        f"tabs and sections disagree: {sorted(set(tabs) ^ set(sections))}"
    )

    # The rail is read as a setup runs: a provider holds the key, a model is
    # chosen on one, and the prompt is added after both.
    order = [page.index(f'data-tab="{tab}"') for tab in ("providers", "models", "prompt")]
    assert order == sorted(order), "the rail offers models before the providers they need"

    assert 'aria-current="page"' in page, "the rail does not mark the current section"
    assert 'button.setAttribute("aria-current"' in script, (
        "showTab does not mark the current section for assistive technology"
    )
    assert 'button.classList.toggle("active"' not in script, (
        "the current section is a class the accessibility tree cannot see"
    )

    show = re.search(r"function showTab\(name, remember = true\)\s*\{(.*?)\n\}", script, re.S)
    assert show, "showTab() is gone; this test would pass vacuously"
    # Comments out: the function explains this rule in prose, and the prose
    # names the assignment it is warning against.
    code = re.sub(r"//.*", "", show.group(1))
    assert 'history.replaceState(null, "", `#${name}`)' in code, (
        "the section is not written into the URL"
    )
    assert "location.hash =" not in code, "writing the hash as a property fires a second switch"
    assert 'showTab(loaders[wanted] ? wanted : "overview", false)' in script, (
        "a reload does not land on the section the hash names"
    )


def test_the_rail_carries_the_attention_items_to_their_sections():
    """The overview knows an error is waiting on Events; the rail says so.

    An attention item names a tab and the card already draws a `go` button for
    it. The badge is the same fact in the navigation, so an operator who is not
    on Overview can see that something is waiting without going back to find
    out. Every tab an item can name therefore needs a badge host to land in — a
    tab with no host is an item whose destination silently loses its mark.
    """

    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()

    labels = re.search(r"const tabLabels = \{(.*?)\n\};", script, re.S)
    assert labels, "tabLabels is gone; this test would pass vacuously"
    named = set(re.findall(r"^\s+([a-z]+):", labels.group(1), re.M))
    assert named, "tabLabels names no tab"

    badges = set(re.findall(r'data-badge="([a-z]+)"', page))
    assert badges == named, (
        f"a badge host and a tab an item may name disagree: {sorted(badges ^ named)}"
    )

    attention = re.search(r"function paintAttention\(items\)\s*\{(.*?)\n\}", script, re.S)
    assert attention, "paintAttention() is gone; this test would pass vacuously"
    assert "paintBadges(items)" in attention.group(1), (
        "the rail is not told what the attention card found"
    )


def test_a_role_is_a_form_not_a_json_document():
    """A role is a partial fragment, and partial is the point.

    A scalar the role does not set inherits from the layer below, so each has an
    inherit option. A list only adds, so it is written when non-empty and needs
    no toggle: an empty list and an absent one compose alike.
    """

    script = (WEB / "admin.js").read_text()

    match = re.search(r"function fragmentEditor\(value, .*?\n\}\n", script, re.S)
    assert match, "fragmentEditor() is gone; this test would pass vacuously"
    body = match.group(0)
    assert "clearableSwitch(options, out[field]" in body, "a scalar cannot be left unset"
    assert "if (values.length) fragment[field] = values" in body, "an empty list is written"

    role = re.search(r"async function roleDialog\(existing\)\s*\{(.*?)\n\}", script, re.S)
    assert role, "roleDialog() is gone; this test would pass vacuously"
    assert "fragmentEditor(" in role.group(1), "a role is not edited with the fragment form"
    assert "saveBaseline(" in role.group(1), "a role save does not write the baseline"
    assert "name.disabled" not in role.group(1), "an existing role's name cannot be edited"
    assert "/api/admin/roles/" in role.group(1), "a rename does not call the rename route"


def test_appliance_diagnostics_are_not_offered_as_grants():
    """`env_probe` is a route's own question, not a capability.

    The panel draws every grant surface from the registry catalog, which
    includes diagnostics. Offering one invites exactly the grant D33 says does
    not exist; the matrix and the fragment editor filter them, and the matrix
    names what it left out so the absence is stated rather than mysterious.
    """

    script = (WEB / "admin.js").read_text()

    matrix = re.search(r"function renderToolMatrix\(\)\s*\{(.*?)\n\}", script, re.S)
    assert matrix, "renderToolMatrix() is gone; this test would pass vacuously"
    body = matrix.group(1)
    assert "!tool.diagnostic" in body, "a diagnostic is offered as a grant"

    editor = re.search(r"function fragmentEditor\(value, (.*?)\n\}\n", script, re.S)
    assert editor, "fragmentEditor() is gone; this test would pass vacuously"
    assert ".diagnostic" in editor.group(1), (
        "a role or defaults fragment can grant a diagnostic"
    )


def _function(script: str, name: str) -> str:
    match = re.search(rf"(?:async )?function {name}\([^)]*\)\s*\{{(.*?)\n\}}", script, re.S)
    assert match, f"{name}() is gone; this test would pass vacuously"
    return match.group(1)


def test_models_and_providers_read_the_catalog_and_the_document_fresh():
    """The other tab's tiles change what this one can offer (D76)."""

    script = (WEB / "admin.js").read_text()

    for name in ("loadModels", "loadProviders"):
        body = _function(script, name)
        assert "loadBaseline(true)" in body, f"{name} reads a stale document"
        assert "loadPresets(true)" in body, f"{name} offers stale tiles"
    assert '"/api/admin/catalog"' in _function(script, "loadPresets")


def test_a_preset_provider_is_added_with_a_key_and_nothing_else():
    """The tile's whole form is the key, and the key goes to the route that files it."""

    script = (WEB / "admin.js").read_text()

    dialog = _function(script, "providerPresetDialog")
    assert '"/api/admin/providers"' in dialog
    assert "preset: preset.id" in dialog
    assert "api_key: key.value" in dialog
    assert "keyInput()" in dialog

    key = _function(script, "keyInput")
    assert 'input.type = "password"' in key, "a pasted key is drawn in clear"
    assert 'input.autocomplete = "off"' in key, "the browser offers to remember a key"


def test_no_form_asks_for_a_key_file_path():
    """D77: the panel takes a key, and the server decides where it lives."""

    script = (WEB / "admin.js").read_text()

    assert "api_key_file =" not in script, "a form writes a key path into the baseline"
    assert "Key file" not in script


def test_a_key_is_replaced_through_its_own_route():
    """A key is pasted into the provider's panel and filed by the route that owns it.

    The panel is where an operator can see which provider they are replacing it
    for; the server decides where the file lives, and nothing reads the old one
    back (D77).
    """

    script = (WEB / "admin.js").read_text()

    body = _function(script, "providerPanel")
    assert '"PUT", `/api/admin/providers/${encodeURIComponent(name)}/key`' in body
    assert "api_key: key.value.trim()" in body


def test_a_provider_in_use_cannot_be_deleted():
    """The rule lives in the panel, which is now the only place a provider is drawn.

    A provider still named by a model is refused here rather than deleted and
    left dangling, which would surface as a 401 at the first turn.
    """

    script = (WEB / "admin.js").read_text()

    body = _function(script, "providerPanel")
    assert "In use by ${served.join" in body, "the panel does not say what uses it"
    assert "if (!served.length)" in body, "the delete is offered for a provider in use"
    assert "delete draft.providers[name]" in body


def test_a_model_is_added_from_a_preset_on_a_provider_it_has():
    script = (WEB / "admin.js").read_text()

    tiles = _function(script, "loadModels")
    assert "route.available" in tiles, "a model is offered on a provider the appliance lacks"
    assert "disabled: !options.length" in tiles

    dialog = _function(script, "modelPresetDialog")
    assert '"/api/admin/models"' in dialog
    assert "preset: preset.id" in dialog


def test_a_preset_models_facts_are_drawn_but_not_editable():
    """The server refuses a contradicted fact; the form does not invite one."""

    script = (WEB / "admin.js").read_text()

    body = _function(script, "hostedModelPanel")
    for field in (
        "providerSelect.disabled = fromPreset",
        "providerModel.disabled = fromPreset",
        "context.disabled = fromPreset",
        "vision.input.disabled = fromPreset",
    ):
        assert field in body, f"a preset fact is editable: {field}"
    assert "draft.models[name] = entry" in body
    assert "deleteModel(name, preset, false)" in body, "a hosted model cannot be deleted"
    assert "reply.disabled" not in body, "reply length is the operator's"


def test_the_models_section_is_a_switch_and_its_tiles():
    """No heading, no table, and this machine first.

    The rail already names the section and the tiles are the whole catalog, so a
    heading above them and a list beside them were both saying what a tile says
    itself. The switch comes first because where a model runs decides what
    adding one even means, and it is two equal answers across the card rather
    than a control parked at an edge.
    """

    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()
    styles = (WEB / "admin.css").read_text()

    assert "<h1>Models</h1>" not in page, "the section still has a heading"
    assert 'id="models-catalog"' not in page, "the models table is back"
    assert 'id="models-hint"' not in page, "the paragraph about downloads is back"

    local = page.index('data-where="local"')
    cloud = page.index('data-where="cloud"')
    assert local < cloud, "cloud is offered before this machine"
    assert 'class="admin-segmented admin-where"' in page, "the switch is not the section's"

    where = _function(script, "rememberedWhere")
    assert '=== "cloud" ? "cloud" : "local"' in where, "the section does not open here"

    rule = re.search(r"body\.admin \.admin-where \{([^}]*)\}", styles)
    assert rule and "width: 100%" in rule.group(1), "the switch is not the card's width"
    button = re.search(r"body\.admin \.admin-where button \{([^}]*)\}", styles)
    assert button and "flex: 1" in button.group(1), "the answers do not share the width"


def test_a_tile_opens_the_settings_of_what_it_added():
    """The tile is the object: add, download, or open its settings.

    With no list to carry edit and delete, a model that is already here is
    reached by the tile that added it.
    """

    script = (WEB / "admin.js").read_text()

    chat = _function(script, "loadModels")
    assert "modelPanel(preset.added[0])" in chat, "an added model has no way in"
    assert "modelPresetDialog(preset, options, providerLabel)" in chat
    assert "Object.entries(baseline.models || {})" in chat, (
        "a model declared by hand has no tile"
    )

    local = _function(script, "localTile")
    assert "modelPanel(added[0])" in local, "an added model on this machine has no way in"

    providers = _function(script, "loadProviders")
    assert "providerPanel(names[0])" in providers, "an added provider has no way in"
    assert "Object.entries(baseline.providers || {})" in providers, (
        "a provider declared by hand has no tile"
    )


def test_a_models_settings_are_the_presets_answer():
    """Every setting the entry may carry, and none the server would refuse.

    The window, the reply length and the reasoning default are the entry's; an
    offload, a draft head and a residency are the runtime's. The draft head is
    drawn only where the preset declares one, since a flag a model does not
    have is a setting that silently does nothing.
    """

    script = (WEB / "admin.js").read_text()

    body = _function(script, "localModelPanel")
    assert 'dialogField("Context tokens", context)' in body
    assert 'dialogField("Max output tokens", reply)' in body
    assert 'dialogField("Layers on the GPU", layers)' in body
    assert '"speculation" in starting' in body, "a draft head is offered where none exists"
    assert 'dialogField("Context memory", kvCache)' in body, "the cache type is not offered"
    assert 'dialogField("Residency", residency)' in body
    assert "reasoningField(model)" in body, "the reasoning default is not offered"
    assert "min: 0,\n    max: 999" in body, "the offload is not held to what the runtime takes"

    save = body[body.index("saveBaseline(") :]
    assert "entry.local = {" in save, "the runtime's settings are not written"
    assert "kv_cache: kvCache.value" in save, "the cache type is not written"
    assert 'residency.value === "on-demand" ? { idle_minutes' in save, (
        "an idle period is written for a model that never unloads"
    )
    assert 'dialogDelete("Delete model", () => deleteModel(name, preset, true))' in body, (
        "a model on this machine cannot be deleted from its panel"
    )


def test_a_catalog_write_forgets_every_cached_view_of_it():
    """The role editor's model list is a cache too, and missed it once."""

    script = (WEB / "admin.js").read_text()

    body = _function(script, "catalogChanged")
    assert "presetsUI.catalog = null" in body
    assert "catalogUI.models = null" in body
    assert "baselineUI.document = data.baseline" in body


def test_every_shipped_logo_is_named_by_a_preset():
    """A logo no preset names is a file nobody can see; a preset without one gets a
    monogram, which is the fallback and not an error.
    """

    import json

    presets = WEB.parents[2] / "neuman-server" / "src" / "neuman_server" / "presets"
    named = {
        json.loads(path.read_text())["logo"]
        for path in presets.glob("*/*.json")
        if "logo" in json.loads(path.read_text())
    }
    shipped = {path.stem for path in (WEB / "logos").glob("*.svg")}

    assert named, "no preset names a logo; this test would pass vacuously"
    assert shipped <= named, f"logos no preset names: {sorted(shipped - named)}"
    for path in (WEB / "logos").glob("*.svg"):
        text = path.read_text()
        assert "<script" not in text and "href=" not in text, f"{path.name} is not inert"


def test_a_baseline_save_sends_the_document_it_was_based_on():
    """The guard against four tabs clobbering each other. The server compares
    the base with what it holds and answers 409; the client reloads.
    """

    script = (WEB / "admin.js").read_text()

    match = re.search(r"async function saveBaseline\(mutate\)\s*\{(.*?)\n\}", script, re.S)
    assert match, "saveBaseline() is gone; this test would pass vacuously"
    body = match.group(1)
    assert "/api/admin/baseline" in body, "the save does not write the baseline"
    assert "{ baseline: next, base }" in body, "the save does not carry the document it read"
    assert "baseline_changed" in body, "a lost race is not noticed"
    assert "loadBaseline(true)" in body, "a lost race does not reload"


def test_the_account_page_shows_roles_in_force_and_has_no_baseline_editor():
    """The grant is a role; the baseline has no account policy editor."""

    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()

    overview = re.search(r"function renderAccountOverview\(\)\s*\{(.*?)\n\}", script, re.S)
    assert overview, "renderAccountOverview() is gone; this test would pass vacuously"
    assert "renderRolesInForce()" in overview.group(1), "roles in force are not drawn"

    roles = re.search(r"function renderRolesInForce\(\)\s*\{(.*?)\n\}", script, re.S)
    assert roles, "renderRolesInForce() is gone; this test would pass vacuously"
    assert "remove: held.direct ?" in roles.group(1), (
        "a group-granted role is offered a removal the account cannot make"
    )
    assert "account-role-conflicts" in roles.group(1), "role conflicts are not shown"

    assign = re.search(r"async function addDeclaredRole\(.*?\n\}", script, re.S)
    assert assign, "addDeclaredRole() is gone; this test would pass vacuously"
    assert '"/api/admin/policy/preview"' in assign.group(0), (
        "role assignment can save a conflict without previewing it"
    )
    toggle = re.search(r"async function toggleDeclaredRole\(.*?\n\}", script, re.S)
    assert "saveDeclaredRoles(" in toggle.group(0), (
        "a role assignment does not write the baseline"
    )
    assert 'entry.source === "declared"' in toggle.group(0), (
        "a group-granted role is written as directly assigned"
    )

    assert "declaredPolicyDialog" not in script, "the baseline policy is editable again"
    assert 'id="account-declared' not in page, (
        "the account page still has a baseline policy card"
    )
    assert "account-declared" not in script, "the baseline policy card is still rendered"


def test_new_account_offers_role_presets_and_previews_them():
    """Onboarding assigns roles; the review is composed by the server."""

    script = (WEB / "admin.js").read_text()

    dialog = re.search(r"async function newAccountDialog\(\)\s*\{(.*?)\n\}", script, re.S)
    assert dialog, "newAccountDialog() is gone; this test would pass vacuously"
    body = dialog.group(1)
    assert "rolePresetField(" in body, "there are no role cards in the create flow"
    assert 'api("POST", "/api/admin/policy/preview"' in body, (
        "the create flow does not preview the starting roles"
    )
    assert "roles: chosenRoles" in body, "the selected roles are not sent"
    assert "groups: chosenGroupNames" in body, "the selected groups are not sent in one write"
    assert "fragmentSummary(draft.policy)" in body, "the preview does not summarise the policy"


def test_the_prompt_has_its_own_tab():
    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()
    assert 'id="tab-prompt"' in page, "there is no prompt tab"
    assert 'id="prompt"' in page, "the prompt is gone"
    assert "async function loadPrompt()" in script, "the prompt has no loader"
    assert 'api("PUT", "/api/admin/prompt"' in script, "the prompt cannot be saved"


def test_the_window_is_branded_with_the_appliances_name():
    """A renamed appliance must not still introduce itself as Neuman (D82).

    The name is read before sign-in as well, because the door is the first
    place it is read and a business that renamed its agent should not find the
    old one on the login screen. It is *set* in the console, on the console's
    wordmark: the name is one for the appliance, an administrator chooses it,
    and every window reads the one it chose.
    """

    page = (WEB / "index.html").read_text()
    script = (WEB / "neuman.js").read_text()
    admin = (WEB / "admin.html").read_text()
    admin_script = (WEB / "admin.js").read_text()

    assert 'id="login-title"' in page, "the sign-in heading has no id to brand"
    assert 'id="sessions-brand"' in page, "the rail has no wordmark to brand"
    assert "function applyAgentName(" in script, "the chat window has no branding"
    assert 'api("GET", "/api/brand")' in script, "the name is not read before sign-in"

    # It is *set* in the console, on its wordmark: one name for the appliance,
    # chosen by an administrator, the same for everyone who signs in.
    assert 'id="admin-brand-name"' in admin, "the console's own brand has no id"
    assert "function beginNameEdit()" in admin_script, "the wordmark cannot be clicked"
    assert 'api("PUT", "/api/admin/name"' in admin_script, "the name cannot be saved"
    assert 'api("PUT", "/api/admin/name"' not in script, (
        "the chat window writes the name; it is the console's to set"
    )


def test_the_administration_page_is_reached_from_the_rail():
    """D91: the door is on the chat rail, where the page's own way back is.

    Settings is the account surface and its `tablist` holds tabs, so the link
    being anywhere inside it is the bug this replaced. The label carries the
    appliance's name, which is the half that lives in the script.
    """

    page = (WEB / "index.html").read_text()
    script = (WEB / "neuman.js").read_text()
    admin = (WEB / "admin.html").read_text()
    admin_script = (WEB / "admin.js").read_text()

    rail = re.search(r'<aside id="sessions".*?</aside>', page, re.S).group(0)
    nav = re.search(r'<nav id="settings-nav".*?</nav>', page, re.S).group(0)
    # The element, not its opening tag as it happens to be written: the door
    # carries a `class="hidden"` the script toggles, so pinning the literal tag
    # made this fail over an attribute that has nothing to do with the claim.
    door = re.search(r'<a\b[^>]*\bid="admin-link"[^>]*>', rail)
    assert door and 'href="/admin.html"' in door.group(0), "the rail has no door"
    assert "admin-link" not in nav, "the appliance is back among the account's tabs"

    # The row, not only the link, or a non-administrator gets an empty footer.
    # It holds Tasks for everyone and Manage for operators, so the row is shown
    # unconditionally and only the Manage link is gated — hiding the row for a
    # non-operator would hide the one door they do have.
    assert '<div id="sessions-foot" class="hidden">' in rail
    assert '$("sessions-foot").classList.remove("hidden")' in script
    assert '$("admin-link").classList.toggle("hidden", !isAdmin)' in script

    assert '$("admin-link-label").textContent = `Manage ${label}`' in script
    assert "<span>manage</span>" in admin, "the page's subtitle says something else"
    assert "document.title = `Manage ${label}`" in admin_script


def test_the_prompt_tab_shows_the_composed_prompt():
    """The tab edits the free text and shows the result, in that order.

    The composition is read-only on purpose: the sections that interpolate live
    facts — the reach, the tool list, the approval mode — are stated by the
    server and must not become fields, or a saved value could disagree with
    what the gate does. What an operator can edit is the free text above it.
    """

    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()
    assert 'id="compose-account"' in page, "the composed prompt takes no account"
    assert 'id="composed-sections"' in page, "there is nowhere to render the sections"
    assert "loadComposed()" in script, "the composed prompt has no loader"
    assert 'api("GET", `/api/admin/prompt/compose?' in script, (
        "the page does not ask the server to compose"
    )
    # The provenance labels are the point: an operator asking "why is this box
    # empty" must not have to read `prompt.py` to find out.
    assert "PROMPT_SOURCES" in script, "the sections are not labelled by source"
    assert "admin-compose-source" in script, "the section headings are unstyled"


def test_a_list_page_keeps_its_controls_off_the_table():
    """Search and the new button float above the card, the count below it."""

    page = (WEB / "admin.html").read_text()

    for tab, search, new in (
        ("accounts", "account-search", "new-account"),
        ("roles", "role-search", "new-role"),
        ("groups", "group-search", "new-group"),
    ):
        section = re.search(rf'<section id="tab-{tab}".*?</section>', page, re.S).group(0)
        assert "admin-page-head" not in section, f"{tab} has its page head back"
        toolbar = section.index('<div class="admin-toolbar">')
        card = section.index('<div class="admin-card admin-list-card">')
        table_end = section.index("</table>")
        assert toolbar < card, f"the {tab} controls are not above the card"
        assert section.index(f'id="{search}"') < card, f"{tab}: the search is on the card"
        assert section.index(f'id="{new}"') < card, f"{tab}: the button is on the card"
        close = section.index("</div>", table_end)
        assert close < section.index('<div class="admin-pager">'), f"{tab}: count on the card"


def test_the_arrows_appear_only_when_there_is_a_second_page():
    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()
    body = re.search(r"function renderAccountPager\(.*?\n\}", script, re.S).group(0)

    pager = re.search(r'<div class="admin-pager">(.*?)</div>', page, re.S).group(1)
    order = [pager.index(f'id="accounts-{part}"') for part in ("page", "prev", "next")]
    assert order == sorted(order), "the arrows do not follow the range"
    assert 'classList.toggle("hidden", !paged)' in body, "the arrows always show"
    assert 'counted(accountsUI.total, "account")' in body, "a single page has no total"


def test_a_slow_roster_answer_does_not_paint_over_a_newer_one():
    """The list follows the keyboard, so an earlier query can answer last (D84)."""

    script = (WEB / "admin.js").read_text()
    body = re.search(r"async function refreshAccounts\(.*?\n\}", script, re.S).group(0)

    assert "const asked = ++accountsUI.asked;" in body, "a request is not numbered"
    assert body.index("if (asked !== accountsUI.asked) return;") < body.index(
        "renderAccountRows"
    ), "a stale answer still paints"


def test_the_rail_reads_accounts_roles_groups():
    page = (WEB / "admin.html").read_text()

    rail = [page.index(f'data-tab="{tab}"') for tab in ("accounts", "roles", "groups")]
    assert rail == sorted(rail), "the rail is out of order"


def test_a_group_is_authored_on_the_groups_tab():
    """D85: roles and members are one dialog, and the Roles tab only defines roles."""

    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()

    roles = re.search(r'<section id="tab-roles".*?</section>', page, re.S).group(0)
    assert "<h2>Role grants</h2>" not in roles, "grants are still on the Roles tab"

    groups = re.search(r'<section id="tab-groups".*?</section>', page, re.S).group(0)
    assert 'id="new-group"' in groups and 'id="group-search"' in groups

    assert "prompt(" not in script.split("// -- groups")[1].split("// -- the baseline")[0], (
        "membership is typed from memory again"
    )


def test_a_save_that_was_refused_resumes():
    """Each write is recorded as it lands, so Save again applies only the rest."""

    script = (WEB / "admin.js").read_text()
    body = re.search(r"async function saveGroup\(.*?\n\}", script, re.S).group(0)

    assert "held.members.delete(account);" in body and "held.members.add(account);" in body
    assert "held.roles.delete(role);" in body and "held.roles.add(role);" in body
    assert body.index('"DELETE", "/api/admin/grants"') < body.index(
        '"POST", "/api/admin/grants"'
    ), "a role being removed in this save can refuse the one replacing it"
    assert body.index('await api("POST", path') < body.index("`${path}/${encodeURIComponent"), (
        "removing first leaves no way to replace the last administrator in one save"
    )


def test_the_member_picker_lists_accounts_before_a_letter_is_typed():
    script = (WEB / "admin.js").read_text()
    picker = re.search(r"function listPicker\(.*?\n\}", script, re.S).group(0)
    members = re.search(r"function memberPicker\(.*?\n\}", script, re.S).group(0)

    assert "  refresh();\n  return { search, list, refresh };" in picker, (
        "the list starts empty"
    )
    assert 'if (query) params.set("q", query);' in members, "an empty search asks for nothing"
    assert 'event.key !== "Enter"' in picker, "Enter does not take the closest match"


def test_explanations_are_tooltips():
    """D85: a section's explanation sits behind a `?`, reachable by keyboard."""

    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()

    assert "A group is how roles reach" not in page, "an explanation is back on the page"
    assert "<p>Named policy" not in page
    assert page.count('class="admin-tip" data-tip="') >= 10, "the tips are gone"
    arm = re.search(r"function armTip\(.*?\n\}", script, re.S).group(0)
    assert "node.tabIndex = 0;" in arm, "a tip cannot be reached from the keyboard"
    assert 'id="tip"' in page, "the tooltip has nowhere to draw"


def test_back_to_accounts_uses_the_rail_arrow():
    page = (WEB / "admin.html").read_text()

    back = re.search(r'<button id="account-back".*?</button>', page, re.S).group(0)
    rail = re.search(r'class="admin-rail-link".*?</svg>', page, re.S).group(0)
    assert re.search(r"<path.*</svg>", rail).group(0) in back, "the arrows differ"
    assert "←" not in back


def test_a_role_a_group_gives_shows_where_it_came_from():
    """D86: the pill stays after its `×`, so it has to say why."""

    script = (WEB / "admin.js").read_text()
    roles = re.search(r"function renderRolesInForce\(.*?\n\}", script, re.S).group(0)

    assert "`via ${held.groups.join" in roles, "a role that stayed reads as a failed removal"


def test_an_account_reads_its_membership_back():
    """Three places change it, so `refreshAccount` is the one that knows."""

    script = (WEB / "admin.js").read_text()
    body = re.search(r"async function refreshAccount\(.*?\n\}", script, re.S).group(0)

    assert '"/api/admin/groups"' in body and "account.groups = groups.groups" in body
    assert "account.groups = account.admin" not in script, "membership is patched in place"


def test_a_role_is_not_deleted_from_a_page_that_never_read_the_grants():
    script = (WEB / "admin.js").read_text()
    body = re.search(r"async function deleteRole\(.*?\n\}", script, re.S).group(0)

    assert body.index('"/api/admin/grants"') < body.index("roleMembers("), (
        "an in-use check over grants never loaded passes for any role a group holds"
    )


def test_an_open_add_search_can_be_left_three_ways():
    """N178: the pill reads Cancel, and pressing away or Escape closes it too."""

    script = (WEB / "admin.js").read_text()
    body = re.search(r"function addToken\(.*?\n\}", script, re.S).group(0)

    assert 'add.textContent = "Cancel";' in body, "the open pill does not say how to leave"
    assert 'addEventListener("focusout"' in body, "pressing away does not close it"
    assert 'event.key !== "Escape"' in body, "Escape does not close it"
    assert "add.onmousedown" in body, "a press on Cancel closes it and the click reopens it"
    assert ".onfocusout" not in script and ".onfocusin" not in script, (
        "neither is a property, so an assignment to one does nothing"
    )


def test_a_field_source_does_not_hold_a_column():
    """A control wider than a third column's leftovers drew under the source."""

    css = (WEB / "admin.css").read_text()

    field = re.search(r"\.admin-field \{(.*?)\n\}", css, re.S).group(1)
    assert "grid-template-columns: 170px minmax(0, 1fr);" in field, "the source has a column"
    assert "container-type: inline-size" in css, "narrow is judged by the window, not the form"
    assert re.search(r"\.admin-field select,.*?max-width:", css, re.S), "a select can overflow"


def test_a_source_is_written_in_words():
    script = (WEB / "admin.js").read_text()
    label = re.search(r"function sourceLabel\(.*?\n\}", script, re.S).group(0)
    inherited = re.search(r"function inheritedSource\(.*?\n\}", script, re.S).group(0)

    assert "sourceWords(source)" in label and "sourceWords(" in inherited, (
        "composition's own labels reach the page"
    )
    assert "} role`" in script, "a role's name reads as if it were a person or a place"


def test_paths_are_added_from_a_pill_only_while_editing():
    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()
    adder = re.search(r"function pathAdder\(.*?\n\}", script, re.S).group(0)

    assert 'id="policy-read-add"' not in page, "the permanent add field is back"
    assert 'pill.classList.toggle("hidden", !editing);' in adder, "shown while read-only"
    assert 'iconButton("save", "Save path"' in adder, "an open adder cannot commit"
    assert 'iconButton("cancel", "Cancel"' in adder, "an open adder has no way out"
    assert 'event.key === "Escape"' in adder, "Escape does not leave the field"
    assert "none granted" not in script, "an empty list says so instead of offering an add"
    blur = re.search(r'addEventListener\("blur".*?\}\);', adder, re.S).group(0)
    assert "add(" not in blur, "pressing away adds a path nobody confirmed"


def test_web_tools_is_a_switch_not_a_sentence():
    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()

    switch = re.search(r'id="policy-network".*?</div>', page, re.S).group(0)
    assert 'data-value="any" aria-pressed="false">On<' in switch
    assert 'data-value="none" aria-pressed="false">Off<' in switch
    assert "network tools may be granted" not in page + script, "the sentence is back"
    assert "every network tool withheld" not in page + script, "the sentence is back"


def test_an_override_is_the_final_say():
    """D87: a list is written whole, and there is no per-field way back."""

    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()
    fragment = re.search(r"function overlayFragment\(.*?\n\}", script, re.S).group(0)

    assert "fragment[field] = [...form[field]];" in fragment, "a list is written as a delta"
    for gone in ("extra", "remove", "only:"):
        assert gone not in fragment, f"the overlay is written with `{gone}`"
    assert "accountsUI.pinned" not in script and "function resetField" not in script
    assert ">Clear all overrides<" in page and "xception" not in page


def test_a_change_the_page_shows_is_not_announced():
    script = (WEB / "admin.js").read_text()

    for said in ("Removed ${account}", "Added ${account}", "Exceptions saved", "Created ${"):
        assert said not in script, f"the top of the page narrates an edit: {said}"


def test_the_web_tools_switch_answers_its_own_row():
    page = (WEB / "admin.html").read_text()
    css = (WEB / "admin.css").read_text()

    assert re.search(r'admin-field-switch">\s*<div class="admin-field-label">Web tools', page)
    assert re.search(
        r"\.admin-field-switch \{\s*grid-template-columns: minmax\(0, 1fr\) auto;", css
    ), "the switch does not reach the far edge"


def test_paths_are_a_list():
    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()

    assert 'id="policy-read" class="admin-paths"' in page, "paths are tags again"
    assert 'id="policy-write" class="admin-paths"' in page, "paths are tags again"
    assert "renderEditableChips" not in script


def test_the_policy_tab_is_the_effective_policy():
    """The overview shows who the account is; this tab shows what it can do."""

    page = (WEB / "admin.html").read_text()
    script = (WEB / "admin.js").read_text()
    overview = re.search(r'<section[^>]*id="account-overview".*?</section>', page, re.S).group(
        0
    )
    policy = re.search(r'<section[^>]*id="account-policy".*?</section>', page, re.S).group(0)

    assert "account-glance" not in overview and "Effective policy" not in overview
    assert "<h2>Effective policy " in policy
    assert ">Add override</button>" in policy
    assert 'id="policy-ceiling"' in policy and 'id="policy-mode"' in policy
    assert "limit-ceiling" not in page + script, "approval is on two tabs"
    assert 'statusItem("Groups"' not in script, "the identity card repeats the card below it"


def test_a_model_wears_its_makers_colour():
    script = (WEB / "admin.js").read_text()
    css = (WEB / "admin.css").read_text()

    assert "tag.dataset.brand = brand" in script
    for stem in ("deepseek", "glm", "qwen"):
        assert (WEB / "logos" / f"{stem}.svg").exists(), f"{stem} is not a logo stem"
        assert f'.admin-model[data-brand="{stem}"]' in css, f"{stem} has no colour"


def test_a_tool_is_answered_like_a_policy_field():
    script = (WEB / "admin.js").read_text()
    body = re.search(r"function renderToolMatrix\(.*?\n\}", script, re.S).group(0)

    assert '[["On", true], ["Off", false]]' in body, "a tool is a checkbox again"
    assert "checkField" not in body


def test_a_tool_summary_is_never_squeezed_into_a_column():
    """Four fixed columns once left a summary one word wide, eight lines tall."""

    css = (WEB / "admin.css").read_text()

    tool = re.search(r"\.admin-tool \{(.*?)\n\}", css, re.S).group(1)
    assert "minmax(0, 1fr)" in tool, "the summary has no share of the row"
    assert re.search(r"@container \(max-width: 560px\) \{\s*\.admin-tool \{", css), (
        "the row does not give up its columns before they crush the summary"
    )


def test_a_week_of_usage_shares_one_scale():
    """Days are comparable only if every bar is measured against the same top."""

    script = (WEB / "admin.js").read_text()
    page = (WEB / "admin.html").read_text()
    body = re.search(r"function drawUsageWeek\(.*?\n\}", script, re.S).group(0)

    assert "(day.settled / top) * 100" in body, "a bar is not on the shared scale"
    assert body.count("const top =") == 1, "a day has a scale of its own"
    assert "armTip(bar," in body, "a bar's figure cannot be read"
    assert "days=${USAGE_DAYS}" in script, "the week is not what the server is asked for"
    assert "Recent sessions" not in page, "the usage tab repeats the sessions tab"


def test_a_tool_that_is_on_says_so_in_colour():
    script = (WEB / "admin.js").read_text()
    page = (WEB / "admin.html").read_text()
    css = (WEB / "admin.css").read_text()

    assert 'element("button", on ? "admin-on" : null, label)' in script
    assert 'class="admin-on" data-value="any"' in page, "web tools does not match the tools"
    assert 'button.admin-on[aria-pressed="true"]' in css
    assert "tool-note" not in page + script, "a note about diagnostics is back"


def test_a_field_left_unset_says_what_it_falls_to():
    """A role takes no stance on a field; the row greys out and says what that means."""

    script = (WEB / "admin.js").read_text()
    page = (WEB / "admin.html").read_text()
    editor = re.search(r"function fragmentEditor\(value, .*?\n\}\n", script, re.S).group(0)
    role = re.search(r"async function roleDialog\(.*?\n\}\n", script, re.S).group(0)
    switch = re.search(r"function clearableSwitch\(.*?\n\}", script, re.S).group(0)

    assert "Inherit" not in script and 'placeholder="Inherit"' not in page
    assert "Built-in" not in script, "a word for not taking a stance is back"
    assert 'chosen = chosen === option ? "" : option;' in switch, "an answer cannot be cleared"
    assert '"admin-field-unset"' in editor, "an unset row looks as if it is in play"
    assert "`Not set · ${gives} ${word}`" in editor, "the value behind an unset field is hidden"
    assert 'gives: "defaults give"' in role
    assert "...baselineUI.builtIn, ...(baseline.defaults || {})" in role, (
        "a role's fallback ignores the defaults it actually falls to"
    )


def test_the_role_form_uses_the_policy_tabs_shapes():
    script = (WEB / "admin.js").read_text()
    editor = re.search(r"function fragmentEditor\(value, .*?\n\}\n", script, re.S).group(0)

    assert 'createElement("textarea")' not in editor, "paths are a text box again"
    assert 'createElement("select")' not in editor, "a choice is a dropdown again"
    assert "renderPathList(" in editor and "tagToggle(" in editor


def test_a_switch_looks_unavailable_until_it_is():
    """Greyed until Add override, as one piece, so nothing invites a press it refuses."""

    css = (WEB / "admin.css").read_text()

    rule = re.search(r"\.admin-segmented:has\(button:disabled\) \{(.*?)\}", css, re.S)
    assert rule and "opacity" in rule.group(1), "a locked switch looks live"
    assert "opacity: 1;" not in re.search(
        r"button\.admin-on\[aria-pressed=\"true\"\] \{(.*?)\}", css, re.S
    ).group(1), "the on colour overrides the locked look"


def test_the_rail_leads_back_to_the_roster():
    """Accounts in the rail is the same way out as the back arrow above an account."""

    script = (WEB / "admin.js").read_text()
    body = re.search(r'\$\("tabs"\)\.addEventListener\("click".*?\n\}\);', script, re.S).group(
        0
    )

    assert 'button.dataset.tab === "accounts" && accountsUI.current' in body
    assert body.index("closeAccount()") < body.index("showTab("), "the roster opens behind it"


def test_the_rail_draws_the_cores_working_set_and_never_doubles_a_row():
    """Active is the account's list; a view here is folded into it, not added beside it.

    Membership, order and unread are the core's (D63), so two windows agree; the
    only thing this window adds is the word `open` and, where it holds a view,
    the phase it has from the frames — which is finer than a four-second poll.
    A session in both places is one row keyed by session id, or the rail would
    draw it twice and flicker between the two answers.
    """

    script = (WEB / "neuman.js").read_text()
    body = re.search(r"function activeGroups\(\)\s*\{(.*?)\n\}\n", script, re.S)
    assert body, "activeGroups() is gone; this test would pass vacuously"
    drawn = body.group(1)

    assert "activeRows" in drawn, "the rail stopped asking the core for the working set"
    assert re.search(r"bySession\.set\(row\.id,", drawn), "the core's rows are not keyed"
    assert "group.views.push(view)" in drawn, "a view here is not folded into its row"
    # And the order is the conversation's, not this window's last touch.
    assert "b.lastActivity - a.lastActivity" in drawn, (
        "the rail orders by when a row was opened"
    )


def test_the_working_set_poll_is_silent_while_the_window_is_hidden():
    """The poll costs a request per window, so a hidden one must not run.

    It is a poll rather than a held request because the client's `/api` proxy
    forwards each call on a worker thread: a request left open would pin one
    thread per window for as long as the window exists, and this feature's whole
    premise is more windows (`neuman-client/src/neuman_client/ui.py`).
    """

    script = (WEB / "neuman.js").read_text()
    schedule = re.search(r"function scheduleActive\(\)\s*\{(.*?)\n\}\n", script, re.S)
    assert schedule, "scheduleActive() is gone; this test would pass vacuously"

    assert re.search(r"if \(!csrf \|\| document\.hidden\) return;", schedule.group(1)), (
        "a hidden window keeps polling"
    )
    # And coming back must read both: the working set *and* the history a pin or a
    # derived title changed while this window was behind another.
    watcher = re.search(r'addEventListener\("visibilitychange".*?\n\}\);', script, re.S)
    assert watcher, "nothing re-reads the list when the window comes back"
    assert "loadSessions();" in watcher.group(0) and "loadActive();" in watcher.group(0)


def test_opening_a_session_is_reading_it_everywhere():
    """The watermark is the account's, so the write goes with showing the session.

    A local flag would clear the badge in this window only, which is the bug the
    column exists for; and a reply that lands in the session already on screen
    has been read too, or the row would say `finished` about something in front
    of the reader.
    """

    script = (WEB / "neuman.js").read_text()

    show = re.search(r"function showView\(view\)\s*\{(.*?)\n\}\n", script, re.S)
    assert show, "showView() is gone; this test would pass vacuously"
    assert "markRead(view.sessionId)" in show.group(1), "opening does not mark it read"

    done = re.search(r'case "done": \{(.*?)\n    \}', script, re.S)
    assert done, "the done case is gone; this test would pass vacuously"
    assert "markRead(view.sessionId)" in done.group(1), (
        "a reply landing in the open session leaves it unread"
    )


def test_a_card_from_another_window_is_answered_on_this_windows_socket():
    """A verdict travels on the branch's socket, which this window must open first.

    A session opened from the projection holds no socket — one is opened when the
    reader sends — so a card staged from the server's pending read would have
    buttons that did nothing. Opening it in `decide` also makes an approval
    recoverable after a dropped connection, and the card must still not be marked
    unless `send` says the frame went out (N89's lesson, in a second window).
    """

    script = (WEB / "neuman.js").read_text()

    decide = re.search(r"async function decide\([^)]*\)\s*\{(.*?)\n\}\n", script, re.S)
    assert decide, "decide() is gone; this test would pass vacuously"
    body = decide.group(1)
    assert "ensureSocket(" in body, "an approval from a window without a socket is dropped"
    assert body.index("ensureSocket(") < body.index("send({"), "the verdict is sent first"
    assert re.search(r"if \(!send\(", body), (
        "a verdict the socket dropped marks the card anyway"
    )

    stage = re.search(r"function stagePending\(call\)\s*\{(.*?)\n\}\n", script, re.S)
    assert stage, "stagePending() is gone; this test would pass vacuously"
    drawn = stage.group(1)
    assert "call.preview" in drawn, "the carried preview is not what the card shows"
    assert "diffView(call.preview)" in drawn, "a write's card shows its arguments, not its diff"


def test_a_card_answered_elsewhere_stops_offering_the_choice():
    """The buttons go when the verdict lands in another window.

    The reply and the outcome reach only the socket that owns the turn, so this
    window cannot know which way it went — but leaving Approve and Deny on a call
    that has already been decided is the N89 shape of bug, where a card offers a
    choice that was made without it.
    """

    script = (WEB / "neuman.js").read_text()
    settle = re.search(r"function settlePending\(view\)\s*\{(.*?)\n\}\n", script, re.S)
    assert settle, "settlePending() is gone; this test would pass vacuously"
    body = settle.group(1)
    assert "remotePending" in body, "it takes away buttons it did not draw"
    assert "ask.remove()" in body, "the buttons stay after the verdict"

    poll = re.search(r"async function loadActive\(\)\s*\{(.*?)\n\}\n", script, re.S)
    assert poll, "loadActive() is gone; this test would pass vacuously"
    assert "settlePending(" in poll.group(1), "nothing clears a card decided elsewhere"
    assert "loadPending(" in poll.group(1), (
        "a prompt starting later never reaches the open card"
    )


def test_a_dragged_file_never_replaces_the_page():
    """D102: Chromium opens a file dropped on a page that did not cancel the drag.

    The handler cancels `dragover` for every file drag, including where a drop is
    not accepted — returning early there would let a stray drop on the login
    screen navigate the tab to the file. And the bytes go to the daemon's own
    route, which is the one place they may land.
    """

    code = (WEB / "neuman.js").read_text(encoding="utf-8")
    over = re.search(r'addEventListener\("dragover", \(event\) => \{(.*?)\n\}\);', code, re.S)
    assert over, "the dragover handler is gone; this test would pass vacuously"
    body = over.group(1)
    assert body.index("event.preventDefault()") < body.index("canDrop()"), (
        "dragover must cancel before deciding whether this view accepts a drop"
    )
    assert "/api/drops?name=" in code
