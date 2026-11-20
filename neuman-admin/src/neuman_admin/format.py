"""Turning a response into something an admin reads rather than parses.

The rule everywhere here: when a shape is not recognised, fall back to
formatted JSON rather than guessing. A formatter that drops an unexpected
field is worse than one that prints raw JSON, because the admin cannot tell
that anything is missing — and on the audit surface in particular, a silently
dropped field is a governance record that reads as complete and is not.

So `render` dispatches on known shapes and defers on everything else. Adding a
route does not require touching this file; it just prints as JSON until
somebody decides it deserves better.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["render"]


def render(command: str, subcommand: str | None, payload: Any) -> str:
    formatter = _FORMATTERS.get((command, subcommand))
    if formatter is None:
        return _raw(payload)
    try:
        return formatter(payload)
    except (KeyError, TypeError, AttributeError):
        # The server sent a shape this does not know. Show it rather than
        # crashing or, worse, printing a confidently wrong subset.
        return _raw(payload)


def _raw(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _table(rows: list[list[str]], headers: list[str]) -> str:
    """A plain aligned table. No box drawing: this gets piped into things."""

    if not rows:
        return "(none)"
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(lines)


def _policy_show(payload: Any) -> str:
    policy = payload["policy"]
    lines = [f"account: {payload['account']}", ""]

    tools = policy.get("tools") or []
    lines.append(f"tools:  {', '.join(tools) if tools else '(none)'}")
    lines.append(f"accept: {policy.get('auto_accept', '(default)')}")
    lines.append("")

    reach = payload.get("reach") or []
    lines.append("reach:")
    if reach:
        lines.extend(f"  {path}" for path in reach)
    else:
        lines.append("  (none)")

    provenance = payload.get("provenance") or {}
    if provenance:
        lines.append("")
        lines.append("provenance:")
        # Where each part of the effective policy came from is the reason this
        # command exists: "why can alice do this" is answered here or nowhere.
        lines.extend(f"  {key}: {value}" for key, value in sorted(provenance.items()))
    return "\n".join(lines)


def _grants(payload: Any) -> str:
    return _table([[g["group"], g["role"]] for g in payload["grants"]], ["GROUP", "ROLE"])


def _audit_search(payload: Any) -> str:
    rows = [
        [
            str(hit.get("seq", "")),
            str(hit.get("at", ""))[:19],
            str(hit.get("actor", "")),
            str(hit.get("action", "")),
        ]
        for hit in payload["hits"]
    ]
    return _table(rows, ["SEQ", "AT", "ACTOR", "ACTION"])


def _audit_verify(payload: Any) -> str:
    if payload.get("ok"):
        return f"chain intact ({payload.get('checked', '?')} entries)"
    # The one result that must never be mistaken for success.
    broken = payload.get("broken_at", "?")
    return f"CHAIN BROKEN at sequence {broken}"


def _sessions(payload: Any) -> str:
    rows = [
        [
            s.get("id", "")[:12],
            s.get("account", ""),
            str(s.get("created_at", ""))[:19],
            (s.get("title") or "")[:48],
        ]
        for s in payload["sessions"]
    ]
    # Ids truncated to twelve: short enough that the title survives in an
    # eighty-column terminal, and the admin read path resolves a unique prefix,
    # so what is printed here is what `session events` accepts. It did not used
    # to — only the full id resolved, and this comment claimed otherwise.
    return _table(rows, ["ID", "ACCOUNT", "CREATED", "TITLE"])


def _session_kinds(payload: Any) -> str:
    counts = payload["kinds"]
    lines = [
        f"session: {payload['session_id']}",
        f"events:  {payload['total_events']}",
        f"span:    {str(payload.get('first_ts'))[:19]} -> {str(payload.get('last_ts'))[:19]}",
        "",
    ]
    lines.extend(f"  {count:>7}  {kind}" for kind, count in counts.items())
    return "\n".join(lines)


def _accounts(payload: Any) -> str:
    # STATE and ROLE rather than booleans: an admin scanning this wants the
    # rows that need attention to read differently. `disabled` is a revocation
    # that is still visible, not a deleted account.
    rows = [
        [
            account.get("name", ""),
            "admin" if account.get("admin") else "user",
            "disabled" if account.get("disabled") else "ok",
            ",".join(account.get("groups") or []),
            # Dates, not timestamps: the column is for "is this account in
            # use", and a second's precision makes the table wider without
            # answering it any better.
            (account.get("created") or "")[:10],
            (account.get("last_seen") or "")[:10],
        ]
        for account in payload["accounts"]
    ]
    return _table(rows, ["ACCOUNT", "ROLE", "STATE", "GROUPS", "CREATED", "LAST SEEN"])


def _models(payload: Any) -> str:
    # VISION is shown because it decides whether `fs_image` works, and its
    # absence here is indistinguishable from a model that cannot see. A blank
    # rather than "no": the column is about which models are special.
    rows = [
        [
            m.get("name", ""),
            m.get("endpoint", ""),
            m.get("provider_model", ""),
            "yes" if m.get("vision") else "",
            # CONTEXT for the same reason VISION is here: a zero is why
            # compaction would not fire, and the only other symptom is a turn
            # dying at the provider (N86). Blank rather than "0" when unknown,
            # so the column reads as "not stated" rather than "no window".
            str(m["context_tokens"]) if m.get("context_tokens") else "",
            str(m["max_tokens"]) if m.get("max_tokens") else "",
        ]
        for m in payload["models"]
    ]
    return _table(rows, ["NAME", "ENDPOINT", "PROVIDER MODEL", "VISION", "CONTEXT", "MAX OUT"])


def _restore(payload: Any) -> str:
    """The chain result first, because it is the part that can be bad.

    A restore reports success and a broken chain as one object, and the
    interesting field is not the one a reader's eye goes to. Leading with it
    is the difference between noticing and not.
    """

    lines = []
    if payload.get("chain_ok"):
        lines.append(f"chain intact ({payload.get('chain_checked', '?')} entries)")
    else:
        seq = payload.get("chain_first_bad_seq", "?")
        lines.append(f"CHAIN BROKEN at sequence {seq} — the restored record is not intact")
    lines.append("")
    lines.append(f"source:   {payload.get('source', '')}")
    loaded = "restored" if payload.get("database_loaded") else "not in backup"
    lines.append(f"database: {loaded}")

    overlays = payload.get("overlay_files") or []
    lines.append(f"overlays: {', '.join(overlays) if overlays else '(none)'}")
    if payload.get("audit_mirror"):
        lines.append(f"mirror:   {payload['audit_mirror']}")
    return "\n".join(lines)


def _export(payload: Any) -> str:
    # The Nix fragment is the product; the account list is context. Printing
    # the fragment alone makes `> policy.nix` do the right thing.
    return payload["nix"]


def _purge_result(payload: Any) -> str:
    if not payload.get("purged"):
        return "nothing to purge"
    return (
        f"purged {payload['purged']} session(s), {payload.get('events', 0):,} event row(s); "
        f"collected {payload.get('images', 0):,} image(s), {payload.get('bytes', 0):,} bytes"
    )


_FORMATTERS = {
    ("policy", "show"): _policy_show,
    ("policy", "export"): _export,
    ("grant", "list"): _grants,
    ("audit", "search"): _audit_search,
    ("audit", "verify"): _audit_verify,
    ("session", "list"): _sessions,
    ("session", "kinds"): _session_kinds,
    ("session", "purge"): _purge_result,
    ("account", "list"): _accounts,
    ("model", "list"): _models,
    ("restore", None): _restore,
}
