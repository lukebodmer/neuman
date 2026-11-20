"""Fetch a URL, as the acting user.

The only tool that needs `network`, and the simplest of them: it fetches,
decodes and truncates. It does no allowlist checking, because there is nothing
to check against — the request goes out on the user's own network, exactly as
one they typed would.

It reads `http_proxy` from the environment, which is what every HTTP client
does: if the user's own environment routes through a proxy, so does this.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

# The manifest caps the spill file at 1 MiB; stop below it so the truncation
# notice below still fits in the file the model reads. Only a preview of the page
# reaches the model inline (D42), so this is how much it can search, not how much
# it is handed.
MAX_BYTES = 1024 * 1024 - 256

# Types that are not text, and that decoding as UTF-8 would turn into mojibake
# the reader has no way to recognise. A PDF is the case that matters: it is the
# obvious way to "get a paper", and this tool cannot extract one. Saying so and
# naming the HTML rendering costs one turn; returning replacement characters
# costs a turn and produces a wrong conclusion.
_BINARY_TYPES = frozenset(
    {
        "application/pdf",
        "application/zip",
        "application/gzip",
        "application/x-gzip",
        "application/x-tar",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-7z-compressed",
        "application/vnd.rar",
        "application/octet-stream",
        # GitHub's codeload serves tarballs as `application/x-gzip`, which the
        # old list missed — so a `.../tar.gz/...` fetch decoded to mojibake and
        # the model read it as a corrupt response rather than a binary it
        # cannot use.
    }
)

# Some origins (arxiv among them) are friendlier to a named client than to the
# default `Python-urllib/x.y` string.
_USER_AGENT = "neuman-web-fetch/0.1"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("web_fetch: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"web_fetch: invalid arguments: {error}", file=sys.stderr)
        return 2

    url = args.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        print("web_fetch: `url` must be http or https", file=sys.stderr)
        return 2

    # `urllib` reads http_proxy/https_proxy from the environment by default,
    # which is exactly the behaviour the proxy design relies on.
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request) as response:
            content_type = response.headers.get_content_type()
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        # An HTTP error is a real answer: the server was reached and said no.
        print(f"web_fetch: {url}: HTTP {error.code} {error.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        # A DNS failure, a refused connection, a proxy that would not tunnel.
        # All of them are answers about the network rather than a malfunction of
        # the tool, so the reason is passed through unchanged.
        print(f"web_fetch: {url}: {error.reason}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"web_fetch: {url}: {error}", file=sys.stderr)
        return 1

    binary = content_type.startswith(("image/", "audio/", "video/")) or (
        content_type in _BINARY_TYPES
    )
    if binary:
        size = f">{MAX_BYTES}" if len(body) > MAX_BYTES else str(len(body))
        print(
            f"web_fetch: {url}: response is {content_type} ({size} bytes), not text. "
            "This tool returns text only; fetch an HTML rendering of the same "
            "resource (for a paper, arxiv.org/html/<id>) or use `browse`.",
            file=sys.stderr,
        )
        return 1

    truncated = len(body) > MAX_BYTES
    text = body[:MAX_BYTES].decode("utf-8", errors="replace")
    sys.stdout.write(text)
    if truncated:
        # On stdout, not stderr: the harness takes `stdout or stderr`, so a note
        # on stderr is discarded the moment there is a body. The reader would
        # get a silently cut page and no way to know it was cut.
        lead = "" if text.endswith("\n") else "\n"
        sys.stdout.write(f"{lead}[truncated at {MAX_BYTES} bytes]\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
