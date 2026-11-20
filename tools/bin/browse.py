"""Drive the browser that lives on the acting user's machine.

Not headless, deliberately. Measured against live sites: headless Chromium with
the default user-agent got **zero** results from DuckDuckGo's HTML endpoint, and
Google returned eight block indicators. A real user-agent fixed DuckDuckGo (107
results) but not Google or Reddit — so headless works per-site, unpredictably,
which is worse than not offering it. `web_fetch` already covers the things that
*are* reliably fetchable.

**The browser is the user's, not the appliance's.** Neuman does not start a
browser and never has a display of its own. `neuman-browser` runs on the user's
machine, starts a real Chromium there, and exposes its Chrome DevTools Protocol
endpoint on a socket `browse` dials — both run on that machine. This tool
connects to that endpoint, opens one tab, and reuses it.

That inversion is what removes the old machinery: no session variables
reconstructed from a uid, no ozone platform guessed from `/run/user`, no profile
rsync, no `--new-window`. A system service trying to reach into a desktop session
was the architecture being papered over.

**One tab, reused.** The tab's id is remembered between dispatches, because this
is a fresh process each time and "the last page" would eventually be the tab the
user is reading Neuman in — which this same browser very likely holds. A lost id
costs one extra tab, not a failed search.

**CDP over plain HTTP and one WebSocket frame type.** No puppeteer, no Node, no
`websockets` package: the tools run on a dependency-free interpreter, and adding
a library that is declared in one place and not another is precisely N47. The
client beside this file is under three hundred lines.
"""

from __future__ import annotations

import base64
import json
import os
import sys

from browse_cdp import CdpError, Endpoint, Session, create_page, has_page

# Where the id of Neuman's own tab is remembered between dispatches.
#
# The browser is the user's and outlives a dispatch; so must the knowledge of
# which tab is Neuman's. Without it, "the tab" is whichever page is last in the
# target list — and since the user is reading Neuman in this same browser, that
# is very likely the tab they are reading it in.
#
# A file rather than anything in the browser because the tool is a fresh process
# every dispatch, and `Target.createTarget` has no caller-supplied name to look up
# afterwards. A lost id costs one extra tab, not a failed search.
TAB_STATE = os.path.expanduser("~/.local/share/neuman/browse-tab")

# What the tool tells an operator to do when nothing answers. The remedy is on
# the client side, because that is where the browser is.
NO_BROWSER = "no browser is connected; start neuman-browser on the machine whose browser Neuman should drive"


def _remembered_tab() -> str | None:
    try:
        with open(TAB_STATE) as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _remember_tab(target_id: str) -> None:
    try:
        os.makedirs(os.path.dirname(TAB_STATE), exist_ok=True)
        with open(TAB_STATE, "w") as handle:
            handle.write(target_id)
    except OSError:
        # Not fatal: forgetting means one more tab next dispatch, which is the
        # symptom this exists to avoid but not a reason to fail the search.
        pass


def _neuman_tab(endpoint: Endpoint) -> str:
    """The id of Neuman's own tab, opening one if there is not one already.

    One tab, reused for the life of the browser. The existing browser is left
    alone: this opens a tab in it rather than a window beside it, and never
    navigates a page Neuman did not open.
    """

    target_id = _remembered_tab()
    if target_id and has_page(endpoint, target_id):
        return target_id
    target_id = create_page(endpoint)
    _remember_tab(target_id)
    return target_id


# -- subcommands --------------------------------------------------------------


def _search(session: Session, args: dict) -> int:
    """DuckDuckGo's HTML endpoint, which returns parseable results with real URLs.

    Ported from the pi tool, including the detail that matters: results link through
    `//duckduckgo.com/l/?uddg=<real-url>`, so the destination has to be pulled out of
    the query parameter rather than read off the href.
    """

    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        print("browse: `query` is required for search", file=sys.stderr)
        return 2
    limit = int(args.get("limit", 10))

    from urllib.parse import quote

    session.navigate(f"https://html.duckduckgo.com/html/?q={quote(query)}")
    session.settle()

    results = session.evaluate(
        """
        (() => {
          const out = [];
          for (const el of document.querySelectorAll('.result')) {
            const a = el.querySelector('a.result__a');
            if (!a) continue;
            let url = '';
            try {
              const p = new URL(a.getAttribute('href'), 'https://duckduckgo.com');
              url = p.searchParams.get('uddg') || (a.href.startsWith('http') ? a.href : '');
            } catch (e) { continue; }
            if (!url || url.includes('duckduckgo.com/l/')) continue;
            const s = el.querySelector('.result__snippet');
            out.push({
              title: a.textContent.trim(),
              url,
              snippet: s ? s.textContent.trim().slice(0, 400) : '',
            });
          }
          return out;
        })()
        """
    )

    if not results:
        # Exit 0: no results is an answer. A non-zero exit is a failed dispatch,
        # which would make an empty search look like a broken tool.
        print("(no results)")
        return 0

    for item in results[:limit]:
        print(f"{item['title']}\n  {item['url']}\n  {item['snippet']}\n")
    return 0


def _open(session: Session, args: dict) -> int:
    url = args.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        print("browse: `url` must be an http or https URL", file=sys.stderr)
        return 2

    session.navigate(url)
    session.settle()
    print(f"{session.evaluate('document.title')}\n{session.evaluate('location.href')}")
    return 0


def _read(session: Session, args: dict) -> int:
    """The page as text, which is what a model can use.

    Not HTML: a model reading markup spends most of its context on tags. Script and
    style elements are dropped outright rather than stripped afterwards, because
    their *contents* are the noisiest part of a modern page.
    """

    url = args.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        session.navigate(url)
        session.settle()

    limit = int(args.get("limit", 40000))
    text = session.evaluate(
        """
        (() => {
          const clone = document.body.cloneNode(true);
          for (const el of clone.querySelectorAll('script,style,noscript,svg')) el.remove();
          return clone.innerText.replace(/\\n{3,}/g, '\\n\\n').trim();
        })()
        """
    )
    if not isinstance(text, str):
        print("browse: the page produced no text", file=sys.stderr)
        return 1

    print(f"# {session.evaluate('document.title')}\n{session.evaluate('location.href')}\n")
    print(text[:limit])
    if len(text) > limit:
        print(f"\n[truncated at {limit} characters of {len(text)}]")
    return 0


def _shot(session: Session, args: dict) -> int:
    """A PNG on disk, for `fs_image` to hand to a vision model.

    The composition is the point: a page that renders entirely in JavaScript defeats
    text extraction, and a screenshot does not. Written to a path rather than
    returned as base64 because the tool channel is text and an image belongs in the
    filesystem where the existing image tool can find it.
    """

    path = args.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        print("browse: `path` must be an absolute path to write the PNG to", file=sys.stderr)
        return 2

    url = args.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        session.navigate(url)
        session.settle()

    data = session.screenshot(full_page=bool(args.get("full_page", False)))
    try:
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(data))
    except OSError as error:
        # The path is inside the account's write surfaces or it is not; the tool
        # host decided before this ran, and saying which path failed is more
        # useful than a traceback.
        print(f"browse: cannot write {path}: {error}", file=sys.stderr)
        return 1

    print(f"wrote {path} ({len(base64.b64decode(data))} bytes)")
    print("Use fs_image to look at it.")
    return 0


def _eval(session: Session, args: dict) -> int:
    """Run JavaScript in the page.

    The sharpest tool here, and the reason `browse` is granted separately: an
    expression is model-chosen code running in a browser with whatever session
    that browser holds. It is included because the alternative is a model that
    cannot interact with a page at all — clicking, scrolling, filling a search
    box — and every safer subcommand above is implemented in terms of it anyway.
    """

    expression = args.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        print("browse: `expression` is required for eval", file=sys.stderr)
        return 2

    result = session.evaluate(expression)
    print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result)
    return 0


_SUBCOMMANDS = {
    "search": _search,
    "open": _open,
    "read": _read,
    "shot": _shot,
    "eval": _eval,
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("browse: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"browse: invalid arguments: {error}", file=sys.stderr)
        return 2

    action = args.get("action")
    handler = _SUBCOMMANDS.get(action)
    if handler is None:
        print(
            f"browse: unknown action {action!r}; expected one of "
            f"{', '.join(sorted(_SUBCOMMANDS))}",
            file=sys.stderr,
        )
        return 2

    # The profile Neuman browses with belongs to the browser the user launched,
    # and is chosen there. This tool only dials whatever endpoint the operator
    # configured, over a socket when one is set and a loopback port otherwise.
    endpoint = Endpoint.from_environment()

    try:
        target_id = _neuman_tab(endpoint)
        with Session(endpoint, target_id=target_id) as session:
            return handler(session, args)
    except CdpError as error:
        print(f"browse: {error}", file=sys.stderr)
        # The remedy is client-side, and saying so once is more useful than the
        # raw connection error alone. A remote deployment additionally needs its
        # tunnel checked; the client is where that state lives.
        print(f"browse: {NO_BROWSER}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
