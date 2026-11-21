"""Runtime settings: the policy/model baseline and the operator's prompt.

The database row is the only source (D68). A fresh appliance starts with both
empty and is configured in the administration page, so a change takes effect
without a rebuild and nothing outside the database can disagree with it.

Values are JSONB. `json.dumps` plus a cast is used rather than a driver-specific
adapter so the store works against the test doubles and a plain connection
alike.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["SettingsStore"]


class SettingsStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def get(self, key: str) -> Any | None:
        cursor = await self._conn.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = await cursor.fetchone()
        if row is None:
            return None
        # JSONB is decoded by the driver into the Python value that was stored:
        # a dict for the baseline, a string for the prompt. Decoding again would
        # treat a legitimate empty prompt as malformed JSON.
        return row["value"]

    async def set(self, key: str, value: Any) -> None:
        await self._conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (%s, %s::jsonb)
            ON CONFLICT (key) DO UPDATE
               SET value = EXCLUDED.value, updated_at = now()
            """,
            (key, json.dumps(value)),
        )

    async def replace_if(self, key: str, *, expected: Any, value: Any) -> bool:
        """Replace a value only if it is still the one the caller read.

        One statement rather than a read followed by a write. The baseline is
        edited from several tabs now, and two operators — or one with two tabs
        open — would otherwise both read version N and the second save would
        erase the first one's role without either seeing a conflict.

        JSONB equality ignores key order, so `expected` may be the document as
        the client received it. False means the row is no longer that value (or
        is absent); the caller answers 409 and the client reloads.
        """

        cursor = await self._conn.execute(
            """
            UPDATE settings
               SET value = %s::jsonb, updated_at = now()
             WHERE key = %s AND value = %s::jsonb
            """,
            (json.dumps(value), key, json.dumps(expected)),
        )
        return cursor.rowcount > 0
