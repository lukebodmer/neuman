"""The settings store is JSONB, and the driver decodes it.

The regression this guards: `get` used to `json.loads` any string it saw,
because a test double might hand back the text. A legitimate empty prompt is
the string `""`, which is not JSON, so the server refused to start. The column
is JSONB, so the value is already the Python object; decoding it again is the
bug.
"""

from __future__ import annotations

import asyncio
import json

from neuman_server.adapters.persistence.settings import SettingsStore


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self._row = row
        self.executed = None

    async def execute(self, sql, params=None):
        self.executed = (sql, params)
        return _Cursor(self._row)


def test_a_jsonb_string_is_not_decoded_again():
    store = SettingsStore(_Conn({"value": ""}))
    assert asyncio.run(store.get("prompt")) == ""


def test_a_jsonb_object_comes_back_as_an_object():
    baseline = {"schema_version": 2, "roles": {}}
    store = SettingsStore(_Conn({"value": baseline}))
    assert asyncio.run(store.get("baseline")) == baseline


def test_a_missing_key_is_none():
    store = SettingsStore(_Conn(None))
    assert asyncio.run(store.get("prompt")) is None


def test_set_writes_json_text_for_the_jsonb_cast():
    conn = _Conn(None)
    asyncio.run(SettingsStore(conn).set("prompt", ""))
    sql, params = conn.executed
    assert "%s::jsonb" in sql
    assert json.loads(params[1]) == ""


class _WriteCursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _WriteConn:
    def __init__(self, rowcount):
        self.rowcount = rowcount
        self.executed = None

    async def execute(self, sql, params=None):
        self.executed = (sql, params)
        return _WriteCursor(self.rowcount)


def test_replace_if_is_a_compare_and_set():
    """One statement with the old value in the predicate.

    The baseline is edited from four tabs now; a read followed by a write lets
    two of them both read version N and the second erase the first. Putting the
    expected document in the `WHERE` is what makes the loss visible as a 409.
    """

    conn = _WriteConn(1)
    replaced = asyncio.run(
        SettingsStore(conn).replace_if("baseline", expected={"a": 1}, value={"a": 2})
    )
    assert replaced is True
    sql, params = conn.executed
    assert "WHERE key = %s AND value = %s::jsonb" in " ".join(sql.split())
    assert json.loads(params[0]) == {"a": 2}
    assert json.loads(params[2]) == {"a": 1}


def test_replace_if_reports_a_lost_race():
    conn = _WriteConn(0)
    replaced = asyncio.run(
        SettingsStore(conn).replace_if("baseline", expected={}, value={"a": 2})
    )
    assert replaced is False
