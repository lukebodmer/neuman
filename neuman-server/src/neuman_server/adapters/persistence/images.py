"""Content-addressed image bytes, and the reference edges that root them.

Implements `decision.harness.ports.ImageRepository` over `session_images` and
`session_image_refs`. The key is the SHA-256 of the
bytes, so a picture written twice is one row and a re-read costs nothing; the
reference rows say which sessions still use it, so an image shared by two
sessions survives one of them being purged and dies when the last does.

`put` writes the bytes and the reference in one transaction. That is what makes
`sweep` safe to run at any time: an image is rooted the instant it exists, so a
collection racing the put cannot delete a picture whose `tool_result` has not
been logged yet.

The bytes are stored decoded. Base64 is a wire encoding; keeping it in the store
would pay its 4/3 inflation on every row for the convenience of one fewer
conversion at the two seams that need one, and would leave the store's "size" a
different number from the picture's.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ...decision.harness.ports import StoredImage, Swept

__all__ = ["ImageStore"]


class ImageStore:
    """The one copy of every image, keyed by content."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def put(self, *, session_id: str, media_type: str, data: bytes) -> StoredImage:
        sha256 = hashlib.sha256(data).hexdigest()
        async with self._conn.transaction():
            # `DO NOTHING` rather than an upsert: identical bytes are the same
            # row, and a media type that disagreed would be a client
            # contradicting itself about a file it just read, not a value worth
            # overwriting.
            await self._conn.execute(
                """
                INSERT INTO session_images (sha256, media_type, data)
                VALUES (%s, %s, %s)
                ON CONFLICT (sha256) DO NOTHING
                """,
                (sha256, media_type, data),
            )
            # The root, in the same transaction as the bytes. `DO NOTHING`
            # because a session re-reading the same file is one edge, not two.
            await self._conn.execute(
                """
                INSERT INTO session_image_refs (session_id, sha256)
                VALUES (%s, %s)
                ON CONFLICT (session_id, sha256) DO NOTHING
                """,
                (session_id, sha256),
            )
        return StoredImage(sha256=sha256, media_type=media_type, data=data)

    async def sweep(self) -> Swept:
        """Delete every image no session references. Returns rows and bytes.

        A mark-and-sweep from the roots: the reference rows are the graph, the
        surviving sessions are the roots because the rows cascade with them, and
        an image no edge reaches is an orphan. Idempotent and safe to run at any
        time — `put` roots an image before it can be referenced, so there is no
        window in which a live picture looks unreferenced. Run by the retention
        pass after sessions are purged, which is the only thing that removes an
        edge.
        """

        cursor = await self._conn.execute(
            """
            DELETE FROM session_images si
             WHERE NOT EXISTS (
                   SELECT 1 FROM session_image_refs r WHERE r.sha256 = si.sha256
             )
            RETURNING octet_length(data) AS n
            """
        )
        rows = await cursor.fetchall()
        return Swept(images=len(rows), bytes=sum(int(row["n"]) for row in rows))

    async def get(self, sha256: str) -> StoredImage | None:
        cursor = await self._conn.execute(
            "SELECT sha256, media_type, data FROM session_images WHERE sha256 = %s",
            (sha256,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return StoredImage(sha256=row["sha256"], media_type=row["media_type"], data=row["data"])
