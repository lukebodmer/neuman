"""The drops store: what a dropped file is called on disk, and when it goes (D102)."""

from __future__ import annotations

import os
import time

import pytest

from neuman_client.drops import GRACE_S, MAX_DROP_BYTES, DropStore


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("report.pdf", "report.pdf"),
        ("../../.bashrc", ".bashrc"),
        ("C:\\Users\\me\\scan.png", "scan.png"),
        ("..", "file"),
        ("", "file"),
        ("line\nbreak.txt", "linebreak.txt"),
    ],
)
def test_a_dropped_name_is_a_basename_and_nothing_more(tmp_path, given, stored):
    store = DropStore(tmp_path / "drops")

    path = store.save(given, b"x")

    assert path.name == stored
    assert path.parent.parent == store.root


@pytest.mark.parametrize("stem", ["n" * 300, "漢" * 300])
def test_a_long_name_keeps_its_extension(tmp_path, stem):
    path = DropStore(tmp_path / "drops").save(stem + ".xlsx", b"x")

    assert path.suffix == ".xlsx"
    assert len(path.name.encode()) <= 200


def test_a_file_over_the_limit_is_not_stored(tmp_path):
    store = DropStore(tmp_path / "drops")

    with pytest.raises(ValueError):
        store.save("big.bin", b"\0" * (MAX_DROP_BYTES + 1))
    assert not store.root.exists()


def test_a_drop_is_private_to_the_account(tmp_path):
    path = DropStore(tmp_path / "drops").save("a.txt", b"x")

    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_an_unattached_drop_survives_its_grace_period(tmp_path):
    """A drop staged in a chat that has not sent its first message is unknown to
    the core, so "nobody attaches it" alone is not enough to delete it."""

    store = DropStore(tmp_path / "drops")
    path = store.save("draft.md", b"x")

    assert store.sweep([]) == []
    assert path.is_file()

    assert store.sweep([], now=time.time() + GRACE_S + 1) == [path]


def test_dropping_a_file_again_restarts_its_grace(tmp_path):
    store = DropStore(tmp_path / "drops")
    path = store.save("a.md", b"x")
    old = time.time() - GRACE_S - 1
    os.utime(path, (old, old))

    store.save("a.md", b"x")

    assert store.sweep([]) == []
