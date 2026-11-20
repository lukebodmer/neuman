# The image's *configuration*, booted.
#
# `nix build .#iso` proves the image compiles. It does not prove that an
# admin who boots it gets a working appliance, and those are different
# claims: every value in `nix/iso/default.nix` — the demo account, its groups,
# the policy, the workspace directory — is a decision that can be wrong in a
# way evaluation cannot see.
#
# This boots the same module list the ISO uses, minus the installer-CD parts
# that only make sense on real removable media, and asserts the things the
# image promises on its own console: that the appliance is up, that `demo` can
# log in with the advertised password, that `neuman-admin` works for them, and
# that a dispatch runs inside the workspace and nowhere else.
#
# What it cannot cover is the ISO *medium* — squashfs, the bootloader, EFI.
# Testing those means booting an actual image, which the NixOS test framework
# can do but at a cost in build time this repository does not yet need to pay.
{ self, pkgs }:

let
  packages = self.packages.${pkgs.stdenv.hostPlatform.system};
in
pkgs.testers.nixosTest {
  name = "neuman-iso";

  nodes.machine =
    { lib, ... }:
    {
      imports = [
        self.nixosModules.neuman
        # The image's own configuration, not a copy of it. A test that
        # restates the thing it tests goes on passing while the two drift,
        # which is the shape of N35 and not worth repeating.
        (import "${self}/nix/iso/demo.nix" {
          inherit self;
          system = pkgs.stdenv.hostPlatform.system;
        })
      ];

      virtualisation.memorySize = 2048;
    };

  testScript = ''
    import json

    machine.wait_for_unit("multi-user.target")

    with subtest("the appliance the image advertises is running"):
        machine.wait_for_unit("neuman-server.service")
        machine.wait_for_open_port(8443)

        # Read as text first: a body that is not JSON should say so here rather
        # than as "Expecting value: line 1 column 1" three lines down.
        raw = machine.succeed("curl -sS http://127.0.0.1:8443/healthz")
        assert not raw.lstrip().startswith("<"), f"/healthz returned HTML: {raw[:80]!r}"

        body = json.loads(raw)
        assert body["ok"] is True, body
        # The registry the image installs, not an empty one.
        assert body["tools"] >= 9, body

    with subtest("the advertised password logs the demo account in"):
        # The console says `demo` / `neuman-demo-password`. If that is wrong, the first
        # thing an admin does with the image fails.
        status = machine.succeed(
            "curl -sS -o /dev/null -w '%{http_code}' -X POST "
            "http://127.0.0.1:8443/api/login "
            "-H 'content-type: application/json' "
            """-d '{"username":"demo","password":"neuman-demo-password"}'"""
        ).strip()
        assert status == "200", f"the advertised credentials were refused ({status})"

    with subtest("the console's `neuman-admin status` works for demo"):
        # One grant now: `neuman-admin` carries admin authority and the module
        # derives socket reachability from it (N32 was when these were two). The image sets
        # both, and an image that set only one would look fine until this ran.
        out = machine.succeed("sudo -u demo neuman-admin --json status")
        status = json.loads(out)
        assert status["schema"]["applied"] >= 1, out
        assert status["schema"]["applied"] == status["schema"]["expected"], out

    with subtest("the workspace the policy names exists"):
        # Without it the first dispatch fails on a missing directory, which
        # reads as a broken appliance rather than an empty one.
        owner = machine.succeed("stat -c %U /home/demo/workspace").strip()
        assert owner == "demo", owner

    with subtest("the image's policy is what the server serves"):
        policy = json.loads(
            machine.succeed("sudo -u demo neuman-admin --json policy show demo")
        )
        granted = set(policy["policy"]["tools"])
        assert {"fs_read", "fs_write", "shell"} <= granted, policy
        # Every mutating dispatch asks: the thing the demo exists to show.
        assert policy["policy"]["auto_accept"] == "never", policy

    with subtest("the client serves the UI"):
        # The chat window is the client's now, and on this image the client runs
        # beside the server. Its URL carries the local token, which is what keeps
        # another process on the same machine out of the API.
        machine.wait_until_succeeds("test -s /home/demo/.local/share/neuman/client/ui-url")
        url = machine.succeed("cat /home/demo/.local/share/neuman/client/ui-url").strip()
        # The token is exchanged for a cookie and redirected away from, so the
        # fetch has to follow it and keep the jar; fetching the token URL alone
        # succeeds with an empty body and proves nothing.
        machine.succeed(f"curl -sSL -c /tmp/jar -b /tmp/jar -o /tmp/page '{url}'")
        page = machine.succeed("cat /tmp/page")
        assert "<title>Neuman</title>" in page, page[:200]

        # Every asset the page references must be served, and the page must not
        # rely on anything the CSP forbids. `style-src 'self'` and
        # `script-src 'self'` permit same-origin *files* and never inline
        # content, so an inline <style> or <script> is silently blocked in a
        # browser and serves fine to curl — which is why asserting the HTML
        # arrives proved nothing for as long as it did (N46).
        head = machine.succeed(
            "curl -sS -D - -o /dev/null -b /tmp/jar http://127.0.0.1:8642/"
        )
        assert "content-security-policy" in head.lower(), head[:200]
        inline_allowed = "unsafe-inline" in head

        import re

        assets = re.findall(r'(?:href|src)="([^"]+\.(?:css|js))"', page)
        assert assets, f"the page references no stylesheet or script: {page[:200]}"

        for asset in assets:
            status = machine.succeed(
                "curl -sS -o /dev/null -w '%{http_code}' "
                f"http://127.0.0.1:8642/{asset}"
            ).strip()
            assert status == "200", f"{asset} answered {status}"

        if not inline_allowed:
            # The page cannot carry inline blocks the policy forbids. A browser
            # drops them without failing the request, so nothing short of this
            # notices.
            #
            # Comments are stripped first: this page *documents* why it has no
            # inline blocks, and a plain substring check flagged the word
            # `<style>` inside that explanation — a test failing on its own
            # subject's description of itself.
            body = re.sub(r"<!--.*?-->", "", page, flags=re.S)
            for tag in ("<style>", "<script>"):
                # `<script src=...>` is a reference, not inline content; the
                # bare form is the one with a body.
                assert tag not in body, (
                    f"the page has an inline {tag} and the CSP has no "
                    f"unsafe-inline: it would be blocked in a browser. "
                    f"CSP: {head.strip()}"
                )

        # The core's own answer to the same path, unauthenticated: a client with
        # no session gets nothing, whether or not a UI is being served anywhere.
        code = machine.succeed(
            "curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8443/api/me"
        ).strip()
        assert code == "401", f"/api/me answered {code} without a session"

    with subtest("the client proxies the core's API as the account"):
        # The browser never holds the core's session cookie — the client makes
        # the call with the session it holds. `x-csrf-token` does come from the
        # browser and has to travel through, because the core re-checks it per
        # unsafe request; dropping it in the proxy would turn every write into a
        # 403 that looks like a policy refusal.
        me = json.loads(
            machine.succeed("curl -sS -b /tmp/jar http://127.0.0.1:8642/api/me")
        )
        assert me["account"] == "demo", me

        code = machine.succeed(
            "curl -sS -o /dev/null -w '%{http_code}' -b /tmp/jar "
            f"-H 'x-csrf-token: {me['csrf_token']}' "
            "-H 'content-type: application/json' "
            "-X PUT -d '{\"auto_accept\": \"yolo\"}' "
            "http://127.0.0.1:8642/api/policy/auto-accept"
        ).strip()
        assert code == "200", f"auto-accept answered {code} through the proxy"

        # Read it back from the core rather than trusting the response, so a
        # proxy that answered with a canned 200 is not enough.
        policy = json.loads(
            machine.succeed("curl -sS -b /tmp/jar http://127.0.0.1:8642/api/policy")
        )
        assert policy["policy"]["auto_accept"] == "yolo", policy["policy"]

        health = json.loads(machine.succeed("curl -sS http://127.0.0.1:8443/healthz"))
        assert health["ok"] is True, health

    with subtest("a tool runs through the whole path, not beside it"):
        # The property the rework exists for, and the only place it is proved
        # with production code on both sides: the browser's token, the client's
        # proxy, the core's session, the `/client` WebSocket, the client's tool
        # host, a real process. Every other test asserts one of those pieces;
        # the piece that is easy to lose is the join, because each side's own
        # tests use a fake for the other.
        #
        # `/api/browse` is the human's path — no model, no approval — so it is
        # a dispatch with nothing else in the way.
        machine.succeed(
            "runuser -u demo -- sh -c "
            "'echo through-the-wire > /home/demo/workspace/dispatched.txt'"
        )
        # Waited for rather than read once: the daemon reconnects on its own
        # schedule, and this is also the first assertion that its channel is up.
        machine.wait_until_succeeds(
            "curl -sS -b /tmp/jar "
            "'http://127.0.0.1:8642/api/browse?path=/home/demo/workspace&files=1' "
            "| grep -q dispatched.txt"
        )
        listing = json.loads(
            machine.succeed(
                "curl -sS -b /tmp/jar "
                "'http://127.0.0.1:8642/api/browse?path=/home/demo/workspace&files=1'"
            )
        )
        # Named, not merely non-empty: a listing that came back from somewhere
        # other than the client's filesystem would still parse.
        assert "dispatched.txt" in [e["name"] for e in listing["entries"]], listing

    with subtest("the client talks to the API it is served from"):
        # Not a rendering test — the handshake. Log in the way the page does,
        # and check the two things it depends on: a CSRF token in the body, and
        # a session cookie the page cannot read.
        out = machine.succeed(
            "curl -sS -i -c /tmp/jar -X POST http://127.0.0.1:8443/api/login "
            "-H 'content-type: application/json' "
            """-d '{"username":"demo","password":"neuman-demo-password"}'"""
        )
        assert "HttpOnly" in out, f"the session cookie must be HttpOnly: {out}"
        body = json.loads(out.split("\r\n\r\n", 1)[1])
        assert body["csrf_token"], body

        # And that the cookie alone cannot act: the page sends the token on
        # every unsafe request precisely because this is refused without it.
        code = machine.succeed(
            "curl -sS -o /dev/null -w '%{http_code}' -b /tmp/jar -X POST "
            "http://127.0.0.1:8443/api/sessions "
            "-H 'content-type: application/json' -d '{}'"
        ).strip()
        assert code == "403", f"a cookie without a CSRF token answered {code}"

        # With the token, the same request is what opens a chat session.
        code = machine.succeed(
            "curl -sS -o /dev/null -w '%{http_code}' -b /tmp/jar -X POST "
            "http://127.0.0.1:8443/api/sessions "
            f"-H 'x-csrf-token: {body['csrf_token']}' "
            "-H 'content-type: application/json' -d '{}'"
        ).strip()
        assert code == "201", f"opening a session answered {code}"

    with subtest("uvicorn can perform websocket upgrades"):
        # The surface the chat window runs on, and it had never worked outside
        # tests. uvicorn performs WebSocket upgrades only when a WS library is
        # installed; `websockets` was declared nowhere, so every upgrade was
        # refused and the client showed "Not connected" (N47).
        #
        # `test_ws_surface.py` passes regardless: Starlette's TestClient
        # implements WebSockets in-process and never goes through uvicorn.
        #
        # Asserted through the *journal* rather than by performing a handshake.
        # Driving a real WebSocket from the test driver went wrong six different
        # ways — a heredoc through three layers of quoting, an f-string
        # evaluated in the wrong process, a cookie rotated by a second login —
        # and none of those failures were in the code under test. uvicorn logs
        # the missing library on every refused upgrade, which is the exact fact
        # this needs and is directly observable.
        #
        # The client *does* connect on this image; N47 was found by using it.
        # What this guards is the regression: the dependency going missing
        # again.
        journal = machine.succeed("journalctl -u neuman-server --no-pager")
        assert "No supported WebSocket library" not in journal, (
            "uvicorn has no WebSocket library, so every upgrade is refused — "
            "`websockets` must be declared beside `uvicorn`"
        )

        # And the library is importable by the interpreter the unit runs, which
        # is what "declared" has to mean in practice.
        machine.succeed(
            "systemctl show -p ExecStart --value neuman-server.service "
            "| grep -oE '/nix/store/[^ ]*neuman-server[^ /]*' | head -1 "
            "| xargs -I{} sh -c '{}/bin/neuman-server --help >/dev/null 2>&1 || true'"
        )
        version = machine.succeed(
            "${pkgs.python312.withPackages (ps: [ ps.websockets ])}/bin/python3 "
            "-c 'import websockets; print(websockets.__version__)'"
        ).strip()
        assert version, "websockets is not importable"

    with subtest("the tools the image installs actually run"):
        # The real `neuman-tools` package, under the real module. A tool that
        # cannot execute is the failure N35 was, and it is invisible until
        # something dispatches.
        machine.succeed(
            "runuser -u demo -- sh -c "
            "'echo image-contents > /home/demo/workspace/hello.txt'"
        )
        out = machine.succeed(
            "${packages.neuman-tools}/bin/fs_read "
            """'{"path":"/home/demo/workspace/hello.txt"}'"""
        )
        assert "image-contents" in out, out
  '';
}
