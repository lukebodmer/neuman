"""What this client knows about its link to the appliance, in words for a person.

The page is served by this process, so it stays up whether or not the dispatch
socket does. That is what makes a refusal invisible without this module: the
person signs in, sends a message, and the model simply has no tools, because the
core narrows the advertisement to what a connected client says it can run. The
symptom reads as *"my permissions were taken away"*, not *"my client is old"*
(D111).

So the daemon keeps one `Link`, the UI server hands it to the page, and the page
draws it. The wording lives here rather than in JavaScript for two reasons: it is
the same sentence `neuman-client doctor` prints, and a Python string is testable
without a browser.

**One state, one sentence, one action.** A state with nothing a person can do is
not shown at all — `connecting` is silent, and `unreachable` stays quiet for
fifteen seconds so a server restart or a sleeping laptop does not paint a banner
that teaches people to dismiss the one that matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

__all__ = [
    "CONNECTED",
    "CONNECTING",
    "NO_SESSION",
    "QUIET_S",
    "REFUSED",
    "UNREACHABLE",
    "UPDATING",
    "Link",
    "rpc_versions",
]

CONNECTING = "connecting"
CONNECTED = "connected"
REFUSED = "refused"
UNREACHABLE = "unreachable"
NO_SESSION = "no_session"
UPDATING = "updating"

# How long a failure that a retry can fix has to stand before the page says so.
# A server restart is a few seconds; a laptop waking is none of anyone's
# business. The cost is that a genuinely dead appliance is silent for fifteen
# seconds longer than it has to be, which is the right trade against a banner
# that appears for every restart.
QUIET_S = 15.0

# The client's own way out. `--only-upgrade` so a configured client stays at the
# release the repository has, and `apt update` first because the lists are what
# say a newer one exists: without it a person whose machine had not fetched for a
# week is told they are already current.
UPDATE_COMMAND = "sudo apt update && sudo apt install --only-upgrade neuman-client"


@dataclass(slots=True)
class Link:
    """The daemon's view of its own link, and what the page is told about it."""

    client_release: str = ""
    client_rpc: int = 0
    state: str = CONNECTING
    detail: str = ""
    appliance_release: str | None = None
    appliance_rpc: int | None = None
    accepted: tuple[int, ...] = ()
    # When the current non-connected state began. Kept across repeated failures
    # so the quiet period is measured from the first one, not reset by each
    # retry.
    since: float = field(default_factory=monotonic)

    # -- transitions ------------------------------------------------------

    def connected(
        self,
        *,
        release: str | None,
        rpc: int | None,
        accepted: tuple[int, ...] = (),
    ) -> None:
        self.state = CONNECTED
        self.detail = ""
        self.appliance_release = release
        self.appliance_rpc = rpc
        self.accepted = accepted
        self.since = monotonic()

    def connecting(self) -> None:
        """Trying, with nothing to report yet. The page stays quiet for this."""

        self.state = CONNECTING
        self.detail = ""
        self.since = monotonic()

    def unavailable(self, detail: str) -> None:
        """A failure a retry might fix: the appliance is down, or the network is."""

        if self.state != UNREACHABLE:
            self.since = monotonic()
        self.state = UNREACHABLE
        self.detail = detail

    def signed_out(self) -> None:
        """No session, so there is nothing to refuse — the page's login is the fix."""

        self.state = NO_SESSION
        self.detail = ""
        self.since = monotonic()

    def refused(self, versions: dict[str, Any] | None) -> None:
        """The appliance named a reason, or (older than this field) it did not."""

        self.state = REFUSED
        self.since = monotonic()
        if not isinstance(versions, dict):
            self.detail = ""
            return
        server = versions.get("server") if isinstance(versions.get("server"), dict) else {}
        client = versions.get("client") if isinstance(versions.get("client"), dict) else {}
        self.appliance_release = _text(server.get("release"))
        self.appliance_rpc = _number(server.get("rpc"))
        self.accepted = rpc_versions(versions.get("accepted"))
        # The server echoes what the hello said. Prefer it over our own constant,
        # because it is the number that was actually refused.
        echoed = _number(client.get("rpc"))
        if echoed is not None:
            self.client_rpc = echoed
        if _text(client.get("release")):
            self.client_release = str(client["release"])

    def updating(self, release: str) -> None:
        """A newer package is on disk and this process is about to exit into it."""

        self.state = UPDATING
        self.detail = release
        self.since = monotonic()

    # -- what the page draws ----------------------------------------------

    @property
    def visible(self) -> bool:
        if self.state in (REFUSED, UPDATING):
            return True
        if self.state == UNREACHABLE:
            return monotonic() - self.since >= QUIET_S
        return False

    def title(self) -> str:
        if self.state == REFUSED:
            direction = self.direction()
            if direction == "client":
                return "Neuman needs an update on this computer"
            if direction == "appliance":
                return "The appliance needs an update"
            return "Neuman and the appliance are different versions"
        if self.state == UPDATING:
            return "Neuman updated"
        if self.state == UNREACHABLE:
            return "Can't reach the appliance"
        return ""

    def body(self) -> str:
        if self.state == REFUSED:
            direction = self.direction()
            appliance = self.appliance_release or "a different version"
            client = self.client_release or "this version"
            if direction == "client":
                return (
                    f"The appliance runs {appliance} and this computer's Neuman runs "
                    f"{client}. They can't work together until the client is updated."
                )
            if direction == "appliance":
                return (
                    f"This computer's Neuman runs {client} and the appliance runs "
                    f"{appliance}. Ask whoever administers the appliance to update it — "
                    "nothing here needs changing."
                )
            return (
                "They can't work together until one of them is updated. "
                "neuman-client doctor says which, and whoever administers the "
                "appliance can say which end is older."
            )
        if self.state == UPDATING:
            return (
                f"Version {self.detail} is installed. This window is reconnecting to it "
                "and needs no other step."
            )
        if self.state == UNREACHABLE:
            return self.detail
        return ""

    def command(self) -> str:
        """The one line to run, or empty when the fix is on the other machine."""

        if self.state == REFUSED and self.direction() == "client":
            return UPDATE_COMMAND
        return ""

    def direction(self) -> str | None:
        """Which half is older, from the shapes both ends said they speak.

        Derivable, not guessed: below the smallest accepted shape the client is
        behind, above the largest the appliance is. Anything else — no payload
        from an appliance older than this field — is honestly unknown.
        """

        if self.state != REFUSED or not self.accepted:
            return None
        if self.client_rpc < self.accepted[0]:
            return "client"
        if self.client_rpc > self.accepted[-1]:
            return "appliance"
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "visible": self.visible,
            "title": self.title(),
            "body": self.body(),
            "command": self.command(),
        }


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def rpc_versions(value: Any) -> tuple[int, ...]:
    """The distinct shapes in a `rpc_versions` payload, or () if it is not one.

    Shared with the daemon so the two places that read the same list cannot
    disagree about what a shape is.
    """

    if not isinstance(value, list):
        return ()
    return tuple(sorted({v for v in value if isinstance(v, int) and not isinstance(v, bool)}))
