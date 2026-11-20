"""Read an image so a vision model can see it.

The one tool whose output is not for the model to *read*. Everything else in the
registry returns text the model interprets; this returns base64 bytes that the
harness lifts out of the tool result and attaches to the next request as an
`image_url` part. The model never sees the base64 — it sees the picture.

**Why a separate tool rather than a flag on `fs_read`.** `fs_read` returns text
and the client decodes its stdout as UTF-8 (`errors="replace"`), so raw bytes
arrive mangled. Changing that would change the contract for every tool to serve
one. A distinct tool also means the *gate* can distinguish them: looking at an
image costs tokens in a way reading a file does not, and an operator may want to
grant one without the other.

Re-encoding is deliberately not done here. A tool that resized or transcoded
would need an image library, and `max_output_bytes` already bounds what can come
back — an image too large to send is refused with a message naming the limit,
which the model can act on by asking for a smaller one.
"""

from __future__ import annotations

import base64
import json
import os
import sys

# What the harness will accept as one image part. Well under the transport's 8
# MiB line limit after base64's 4/3 inflation, and far above any screenshot: an
# image larger than this is a photograph or a scan, and sending it would cost
# more tokens than the answer is worth.
MAX_IMAGE_BYTES = 4 * 1024 * 1024

# Magic → the media type a data: URI needs. The model's API cares about this
# string, so it is derived from the bytes rather than from the file extension:
# a `.png` that is actually a JPEG must be labelled JPEG or the provider rejects
# it, and nothing about the filename is trustworthy.
_TYPES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _media_type(head: bytes) -> str | None:
    for magic, media in _TYPES:
        if head.startswith(magic):
            return media
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis"):
        return "image/avif"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1"):
        return "image/heic"
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("fs_image: expected one JSON argument", file=sys.stderr)
        return 2

    try:
        args = json.loads(argv[1])
    except json.JSONDecodeError as error:
        print(f"fs_image: invalid arguments: {error}", file=sys.stderr)
        return 2

    path = args.get("path")
    if not isinstance(path, str) or not path:
        print("fs_image: `path` is required", file=sys.stderr)
        return 2

    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        print(f"fs_image: no such file: {path}", file=sys.stderr)
        return 1
    except PermissionError:
        print(f"fs_image: not permitted: {path}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"fs_image: {path}: {error}", file=sys.stderr)
        return 1

    # Checked before reading, so an enormous file is refused rather than loaded
    # into memory to be refused.
    if size > MAX_IMAGE_BYTES:
        print(
            f"fs_image: too large: {path} is {size} bytes, the limit is {MAX_IMAGE_BYTES}",
            file=sys.stderr,
        )
        return 1

    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except IsADirectoryError:
        print(f"fs_image: not a regular file: {path}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"fs_image: {path}: {error}", file=sys.stderr)
        return 1

    media = _media_type(data[:16])
    if media is None:
        # Named as "not an image" rather than passed through: sending arbitrary
        # bytes to a vision endpoint wastes a request and returns a provider
        # error the model cannot interpret. `fs_read` reports what a file
        # actually is, so the model has somewhere to go next.
        print(
            f"fs_image: not a recognised image format: {path} "
            "(supported: png, jpeg, gif, webp, bmp, avif, heic)",
            file=sys.stderr,
        )
        return 1

    # One line, `media_type base64data`, so the harness can split on the first
    # space without parsing. Not JSON: the payload is most of the line, and
    # wrapping megabytes of base64 in a JSON string would double the escaping
    # work for no gain.
    sys.stdout.write(f"{media} {base64.b64encode(data).decode('ascii')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
