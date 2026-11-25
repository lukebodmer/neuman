"""What the client says when the appliance refuses it, and how it says it.

The refusal is the one failure with no retry that fixes it, so it has to end
somewhere a person can see: a state on the `Link` that the page draws, and the
same sentence `doctor` prints. These tests pin the three parts — the direction
(which half is older), the quiet period (a restart is not an outage), and the
command (the one thing a person can run).
"""

from __future__ import annotations

from neuman_client.link import (
    QUIET_S,
    REFUSED,
    UNREACHABLE,
    Link,
)


def _refused(
    *, client_rpc: int, accepted: list[int], client: str = "0.1.0", appliance: str = "0.2.0"
) -> Link:
    link = Link(client_release=client, client_rpc=client_rpc)
    link.refused(
        {
            "server": {"release": appliance, "rpc": max(accepted)},
            "client": {"release": client, "rpc": client_rpc},
            "accepted": accepted,
        }
    )
    return link


def test_a_client_below_the_floor_is_told_to_update_itself():
    link = _refused(client_rpc=1, accepted=[2])

    assert link.state == REFUSED
    assert link.direction() == "client"
    assert link.visible is True
    assert link.title() == "Neuman needs an update on this computer"
    assert "0.1.0" in link.body() and "0.2.0" in link.body()
    assert link.command() == "sudo apt update && sudo apt install --only-upgrade neuman-client"


def test_a_client_above_the_appliance_is_told_the_appliance_needs_the_update():
    """The other direction, which is just as capable of stranding a person."""

    link = _refused(client_rpc=3, accepted=[1, 2])

    assert link.direction() == "appliance"
    assert link.title() == "The appliance needs an update"
    assert "nothing here needs changing" in link.body()
    assert link.command() == "", "there is nothing to run on this machine"


def test_an_appliance_too_old_to_say_which_end_is_older_says_so():
    """A server from before this payload closes without one; guessing is worse."""

    link = Link(client_release="0.1.0", client_rpc=1)
    link.refused(None)

    assert link.direction() is None
    assert link.title() == "Neuman and the appliance are different versions"
    assert "doctor" in link.body()
    assert link.command() == ""


def test_a_refusal_quotes_the_version_the_appliance_refused():
    """The payload is the server's reading of the hello, which is what was refused."""

    link = Link(client_release="0.1.0", client_rpc=1)
    link.refused(
        {
            "server": {"release": "0.2.0", "rpc": 2},
            "client": {"release": "0.0.9", "rpc": 9},
            "accepted": [10],
        }
    )

    assert link.client_release == "0.0.9"
    assert link.client_rpc == 9
    assert link.appliance_release == "0.2.0"
    assert link.direction() == "client"


def test_a_short_failure_is_quiet_and_a_standing_one_is_not():
    """A server restart is seconds long, and a banner for it trains people to ignore it."""

    link = Link(client_release="0.1.0", client_rpc=1)
    link.unavailable("nothing is answering at https://appliance.example:8443")
    assert link.state == UNREACHABLE
    assert link.visible is False, "the page must not paint a banner for a restart"

    link.since -= QUIET_S + 0.1
    assert link.visible is True
    assert "appliance.example" in link.body()


def test_a_repeated_failure_does_not_reset_the_quiet_period():
    """The period is measured from the first failure, not from the latest retry."""

    link = Link(client_release="0.1.0", client_rpc=1)
    link.unavailable("down")
    first = link.since
    link.unavailable("still down")
    assert link.since == first


def test_connecting_and_connected_are_silent():
    link = Link(client_release="0.1.0", client_rpc=1)
    assert link.visible is False

    link.connected(release="0.2.0", rpc=1, accepted=(1,))
    assert link.state == "connected"
    assert link.visible is False
