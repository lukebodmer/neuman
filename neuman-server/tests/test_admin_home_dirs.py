"""The home folders, through the real app (D104).

What these prove that `test_policy.py` cannot: that the folders a `$HOME` grant
expands to are a `settings` row an administrator edits rather than a deployment
setting, that a save reaches the next policy read without a restart, and that
"Use defaults" is a deletion — so a release can still improve the built-in list
under an appliance that never chose one.
"""

from __future__ import annotations

import pytest
from surface import build_surface
from test_http_surface import _home_baseline, as_peer, authenticate

from neuman_server.decision.policy.paths import DEFAULT_HOME_DATA_DIRS

DEFAULT = list(DEFAULT_HOME_DATA_DIRS)


@pytest.fixture
def surface(monkeypatch, tmp_path):
    return build_surface(monkeypatch, tmp_path)


@pytest.fixture
def client(surface):
    with surface.client() as running:
        yield running


@pytest.fixture
def operator(monkeypatch, surface, client):
    surface.accounts.groups["neuman-admin"].add("alice")
    as_peer(monkeypatch, client, 1001, "alice")
    return client


def test_a_fresh_appliance_uses_the_built_in_folders(operator):
    body = operator.get("/admin/home-dirs").json()

    assert body["dirs"] == DEFAULT
    assert body["default"] == DEFAULT
    # Nothing has been chosen: "Use defaults" has nothing to restore, and the
    # page disables it on this answer.
    assert body["set"] is False


def test_saving_stores_the_folders_and_the_appliance_uses_them(surface, operator):
    response = operator.put("/admin/home-dirs", json={"dirs": ["projects", "business"]})

    assert response.status_code == 200, response.text
    assert response.json()["dirs"] == ["projects", "business"]
    assert response.json()["set"] is True
    # In memory, which is what the next turn reads; the row is what a restart
    # reads, and `load_runtime` is asserted to find it in the startup test.
    assert surface.services.home_data_dirs == ("projects", "business")
    assert surface.settings.values["home_data_dirs"] == ["projects", "business"]

    assert operator.get("/admin/home-dirs").json()["dirs"] == ["projects", "business"]


def test_a_saved_folder_reaches_the_next_policy_read(monkeypatch, tmp_path):
    """The wiring: what the page saves is what `$HOME` expands to next.

    The baseline grants the token, a client is connected with a home, and the
    readable list is read before and after the save. A server that stored the
    row but kept the startup tuple would pass every test above and fail here.
    """

    baseline = _home_baseline(tmp_path, ["$HOME"])
    surface = build_surface(monkeypatch, tmp_path, baseline=baseline)
    surface.services.execution.home_value = "/home/remote"

    with surface.client() as client:
        authenticate(surface, client)
        before = client.get("/api/policy").json()["readable"]
        assert "/home/remote/Documents" in before, before

        surface.accounts.groups["neuman-admin"].add("alice")
        as_peer(monkeypatch, client, 1001, "alice")
        saved = client.put("/admin/home-dirs", json={"dirs": ["business"]})
        assert saved.status_code == 200, saved.text

        after = client.get("/api/policy").json()["readable"]
        assert after == ["/home/remote/business"], after


@pytest.mark.parametrize(
    "dirs",
    [
        [],
        [""],
        ["Documents", ""],
        ["../etc"],
        ["/etc"],
        ["a/b"],
        ["."],
        [".."],
        [" Documents"],
        [42],
        "Documents",
    ],
)
def test_a_folder_that_would_mean_somewhere_else_is_refused(operator, dirs):
    response = operator.put("/admin/home-dirs", json={"dirs": dirs})

    assert response.status_code == 400, response.text
    assert response.json()["error"] == "home_dirs_invalid"


def test_duplicates_collapse_without_reordering(operator):
    body = operator.put(
        "/admin/home-dirs", json={"dirs": ["b", "a", "b"]}
    ).json()

    assert body["dirs"] == ["b", "a"]


def test_use_defaults_deletes_the_row_rather_than_writing_the_default(
    surface, operator
):
    operator.put("/admin/home-dirs", json={"dirs": ["projects"]})

    response = operator.delete("/admin/home-dirs")

    assert response.status_code == 200, response.text
    assert response.json()["dirs"] == DEFAULT
    assert response.json()["set"] is False
    assert "home_data_dirs" not in surface.settings.values
    assert surface.services.home_data_dirs == DEFAULT_HOME_DATA_DIRS


def test_writing_needs_admin_authority(monkeypatch, surface, client):
    """Reachability is the group; authority is the account row (D16).

    `bob` can open the socket and is still refused — the route checks the
    database, not the uid alone.
    """

    as_peer(monkeypatch, client, 1002, "bob")

    response = client.put("/admin/home-dirs", json={"dirs": ["projects"]})

    assert response.status_code == 403, response.text
    assert "home_data_dirs" not in surface.settings.values
