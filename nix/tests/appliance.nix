# The appliance, assembled.
#
# Every VM test before this one proved a seam: the session surface, the admin
# socket, the model gateway. This one proves the *composition* — a machine that
# boots with `services.neuman.enable = true` and comes up with Postgres migrated
# and the server listening on its client socket.
#
# What that catches which nothing before it could: the module. Startup
# ordering, unit hardening, an appliance that starts empty and is configured
# through its admin socket, and the paths every part agrees on. A boundary that
# exists in four separate tests can still fail to exist when the four are
# composed, because the composition is where it is actually configured.
#
# It logs in, opens `/client` and completes the handshake as the account it
# authenticated as — the composition of the module, the account store, the
# session surface and the hub. The dispatch itself, with production code on both sides, is proved in
# `vm-iso`, where a client daemon is running: this test's client is a probe, and
# a probe that ran tools would be a second implementation of the thing it is
# meant to check (N35).
{ self, pkgs }:

let
  packages = self.packages.${pkgs.stdenv.hostPlatform.system};

  # What an operator configures after the first administrator exists, set
  # through the admin socket as `neuman-admin baseline set` sends it. Nothing
  # here is module configuration: a fresh appliance starts empty (D68).
  baseline = pkgs.writeText "baseline.json" (
    builtins.toJSON {
      schema_version = 2;
      defaults.files_read = [ "/usr/share/doc" ];
      roles.engineer = {
        tools = [
          "fs_read"
          "fs_image"
          "fs_list"
          "fs_search"
        ];
        files_read = [ "/srv/eng" ];
        files_write = [ "/srv/eng/work" ];
      };
      roles.capped = {
        auto_accept = "safe";
        # The ceiling, so the user-chosen approval mode is tested against a
        # real limit rather than the permissive default. alice may loosen to
        # "safe" and no further; bob keeps the default ceiling and may reach
        # "yolo", so one appliance exercises both sides.
        auto_accept_max = "safe";
      };
      users = [
        {
          name = "alice";
          roles = [
            "engineer"
            "capped"
          ];
        }
        {
          name = "bob";
          roles = [ "engineer" ];
        }
      ];
      # Two providers on one appliance, because that is the arrangement that did
      # not work before — a single global key meant the second provider received
      # the first one's bearer token. Declared by hand, with key paths a secret
      # manager would place: the custom path the administration page keeps.
      providers = {
        seer-host = {
          url = "https://seer.example.test/v1";
          api_key_file = "/run/neuman-test/seer.key";
        };
        reader-host = {
          url = "https://reader.example.test/v1";
          api_key_file = "/run/neuman-test/reader.key";
        };
      };
      models = {
        seer = {
          provider = "seer-host";
          provider_model = "provider/seer";
          vision = true;
          context_tokens = 131072;
        };
        reader = {
          provider = "reader-host";
          provider_model = "provider/reader";
        };
      };
    }
  );

  # The production client against the production core: the probe logs in the way
  # a daemon does and completes the `/client` handshake.
  clientProbe = pkgs.writeShellScriptBin "neuman-connect" ''
    export PYTHONPATH="${self}/neuman-server/src:${self}/neuman-client/src:${self}/contracts/gen/python"
    export PYTHONDONTWRITEBYTECODE=1
    exec ${
      pkgs.python312.withPackages (ps: [
        ps.argon2-cffi
        ps.jsonschema
        ps.psycopg
        ps.psycopg-pool
        ps.starlette
        ps.uvicorn
        ps.websockets
        ps.httpx
      ])
    }/bin/python3 ${./client_probe.py} "$@"
  '';

in
pkgs.testers.nixosTest {
  name = "neuman-appliance";

  nodes.machine =
    { ... }:
    {
      imports = [ self.nixosModules.neuman ];

      # The OS accounts are only the local admin socket's peer identity. The
      # Neuman accounts are created in the database by the bootstrap below;
      # the names match because one person has one name on this machine.
      users.users.alice = {
        isNormalUser = true;
      };
      users.users.bob = {
        isNormalUser = true;
      };
      # Reachability without authority: carol can open the socket and is
      # refused by the core. Without her, "alice succeeds and bob fails" would
      # be equally consistent with the group check doing nothing.
      users.users.carol = {
        isNormalUser = true;
      };

      services.neuman = {
        enable = true;
        # The runtime host is on every appliance; a VM has no GPU, and the CUDA
        # build is unfree and gigabytes.
        gpu = "none";

        # Reachability only. alice and carol may open the socket; alice is an
        # administrator in the database (bootstrap adds her), carol is not, so
        # carol connects and the core refuses her — the case that proves the
        # two checks are separate.
        adminSocketAccess = [
          "alice"
          "carol"
        ];
      };

      environment.systemPackages = [ clientProbe ];

      virtualisation.memorySize = 2048;
    };

  testScript = ''
    machine.wait_for_unit("multi-user.target")

    with subtest("the appliance comes up"):
        # Postgres first: the server requires it, and a server that started
        # without it would be reporting readiness it does not have.
        machine.wait_for_unit("postgresql.service")
        machine.wait_for_unit("neuman-server.service")
        machine.wait_for_open_port(8443)

    with subtest("the schema is migrated before anything reads a table"):
        # The lifespan runs migrations after opening the pool and before
        # starting jobs. If that ordering were wrong the server would still
        # start, and the first request would fail instead.
        applied = machine.succeed(
            "sudo -u postgres psql -d neuman -tAc "
            "'select count(*) from schema_migrations'"
        ).strip()
        assert int(applied) >= 1, f"no migrations applied: {applied!r}"

    with subtest("health reports the tool set it actually loaded"):
        import json
        body = json.loads(machine.succeed("curl -sS http://127.0.0.1:8443/healthz"))
        assert body["ok"] is True, body
        # The count comes from the registry store path, so a mismatch means
        # the server and the module disagree about which tools exist.
        assert body["tools"] >= 9, body

    with subtest("a fresh appliance starts with nothing configured"):
        # No seed: the module writes no baseline, and the server made an empty
        # one in the database on its first start (D68).
        machine.fail("test -e /etc/neuman/baseline.json")
        stored = json.loads(
            machine.succeed(
                "sudo -u postgres psql -d neuman -tAc "
                "\"select value from settings where key = 'baseline'\""
            )
        )
        assert stored == {"schema_version": 2, "defaults": {}, "roles": {}, "users": []}, stored

    with subtest("the first administrator is created in Neuman's own database"):
        # The OS account is the socket peer; the Neuman account is a row. The
        # bootstrap route is the one admin request a peer may make before an
        # administrator exists, and every account after it goes through the
        # normal guarded route.
        machine.wait_until_succeeds("test -S /run/neuman/admin/admin.sock")
        machine.succeed(
            "printf '%s' 'correct horse battery staple' > /tmp/alice.pw && chown alice /tmp/alice.pw && chmod 600 /tmp/alice.pw"
        )
        machine.succeed(
            "runuser -u alice -- neuman-admin bootstrap --name alice --password-file /tmp/alice.pw"
        )

        # The configuration, set before the other accounts exist so each is
        # created holding the roles the document lists for it.
        machine.succeed("runuser -u alice -- neuman-admin baseline set ${baseline}")
        shown = json.loads(
            machine.succeed("runuser -u alice -- neuman-admin --json baseline show")
        )["baseline"]
        assert shown["roles"]["engineer"]["files_write"] == ["/srv/eng/work"], shown
        assert {user["name"] for user in shown["users"]} == {"alice", "bob"}, shown
        machine.succeed(
            "printf '%s' 'bob-password-123' > /tmp/bob.pw && chown alice /tmp/bob.pw && chmod 600 /tmp/bob.pw"
        )
        machine.succeed(
            "runuser -u alice -- neuman-admin account create bob --password-file /tmp/bob.pw"
        )
        machine.succeed(
            "printf '%s' 'correct horse battery staple' > /tmp/carol.pw && chown alice /tmp/carol.pw && chmod 600 /tmp/carol.pw"
        )
        machine.succeed(
            "runuser -u alice -- neuman-admin account create carol --password-file /tmp/carol.pw"
        )

    with subtest("a role rename moves declared references and group grants"):
        # The SQL move is not covered by the surface tests: they fake the
        # connection. This checks the baseline key, both users' declared roles
        # and the real `policy_grants` table together.
        machine.succeed("sudo -u alice neuman-admin grant add eng engineer")
        renamed = json.loads(
            machine.succeed(
                "sudo -u alice neuman-admin --json role rename engineer full-access"
            )
        )
        assert renamed["grants"] == 1, renamed
        grants = json.loads(
            machine.succeed("sudo -u alice neuman-admin --json grant list")
        )["grants"]
        assert {"group": "eng", "role": "full-access"} in grants, grants
        assert {"group": "eng", "role": "engineer"} not in grants, grants
        shown = json.loads(
            machine.succeed("sudo -u alice neuman-admin --json policy show alice")
        )
        assert {"name": "full-access", "source": "declared", "known": True} in shown[
            "roles"
        ], shown

    with subtest("a client logs in and connects"):
        # The path a real client takes: password login, a session cookie, and the
        # `/client` WebSocket authenticated by it. Registration is by absence of
        # refusal — the handshake answers with the account it resolved.
        payload = json.dumps({
            "server": "http://127.0.0.1:8443",
            "user": "alice",
            "password": "correct horse battery staple",
            "state": "/tmp/neuman-probe",
            "tools": ["fs_read"],
        })
        code, out = machine.execute(f"runuser -u alice -- neuman-connect '{payload}'")
        assert code == 0, f"the probe failed: {out}"
        assert "CONNECTED alice fs_read" in out, f"the client did not connect: {out}"

    with subtest("the working set is answered from the real database"):
        # `active_for_account` is the one query in this tree that composes a
        # lateral over branches, an EXISTS over the event log and the `read_at`
        # column. The server's tests fake the connection, so this is where it
        # runs: the probe created a session through the API and read it back,
        # and a session with no reply must come back `idle` — not `finished`,
        # which is what an inverted watermark comparison would say.
        assert "ACTIVE idle" in out, f"the working set did not answer: {out}"

    with subtest("the admin API still refuses over the network port"):
        # D9 puts admin on a local socket authenticated by peer credentials.
        # The routes are mounted on both apps, so this asserts the TCP side
        # stays shut now that the socket side works — the guard is what makes
        # that true, and a regression here is a governance record served to
        # anyone who can reach the port (N27).
        status = machine.succeed(
            "curl -sS -o /dev/null -w '%{http_code}' "
            "http://127.0.0.1:8443/admin/policy/alice"
        ).strip()
        assert status in ("401", "403"), (
            f"the admin API answered {status} over TCP; it must refuse without "
            "peer credentials"
        )

    with subtest("the admin socket exists and is not world-reachable"):
        machine.wait_until_succeeds("test -S /run/neuman/admin/admin.sock")
        mode = machine.succeed("stat -c %a /run/neuman/admin/admin.sock").strip()
        owner = machine.succeed("stat -c %U:%G /run/neuman/admin/admin.sock").strip()
        assert mode == "660", f"admin socket mode is {mode}, not 660"
        assert owner == "neuman:neuman", owner

    with subtest("an admin is identified by the uid the kernel reports"):
        # The assertion N26 was missing. Every other admin test asserts a
        # refusal, which a surface with no way to succeed passes perfectly —
        # and that is exactly what shipped: `peer_name` read two scope keys
        # nothing ever set, so it raised on every connection.
        #
        # This is the positive case, over the real socket, through the real
        # CLI. If it passes, peer credentials arrive.
        out = machine.succeed("sudo -u alice neuman-admin --json status")
        assert out.strip().startswith("{"), out

    with subtest("reachability is not authority"):
        # carol is in `neuman`, so the kernel lets her connect and the server
        # identifies her — and `require_admin` then refuses, because she
        # holds no admin group. This is the check that would otherwise be
        # invisible: without it, a guard that authorized every connected peer
        # would pass every other test here.
        code, out = machine.execute("sudo -u carol neuman-admin status 2>&1")
        assert code == 1, f"carol got exit {code}, expected 1 (refused): {out}"
        assert "not an admin" in out, out

    with subtest("a non-operator cannot reach the socket at all"):
        # bob is not in `neuman`, so the kernel refuses the connect. The CLI
        # must report that as unavailable (69) rather than as a refusal (1):
        # the operator's next move differs.
        # N20: a predicate's exit code is its answer, so read it with
        # `execute`. `machine.succeed` turns the expected non-zero exit into a
        # build failure — which is what happened the first time this ran.
        code, out = machine.execute("sudo -u bob neuman-admin status 2>&1")
        assert code == 69, f"bob got exit {code}, expected 69 (EX_UNAVAILABLE): {out}"
        assert "neuman" in out, f"the refusal must name the likely cause: {out}"

    with subtest("the CLI writes policy that the server then serves"):
        # A round trip through the socket: the CLI sets an overlay, and the
        # *server* is asked what the effective policy now is. Reading back
        # through the same command would only prove the CLI is consistent
        # with itself.
        # The fragment is written with a heredoc rather than an echo. Quoting
        # JSON through Nix, then the test script, then `sh -c` is three layers
        # of escaping, and the first attempt lost one: the CLI received
        # `{auto_accept:"none"}` and correctly refused it, naming the file and
        # the column. Useful failure, wrong test.
        machine.succeed(
            "cat > /tmp/frag.json <<'EOF'\n"
            '{"auto_accept": "never"}\n'
            "EOF"
        )
        machine.succeed("chmod 0644 /tmp/frag.json")
        machine.succeed("sudo -u alice neuman-admin policy set alice /tmp/frag.json")

        shown = json.loads(
            machine.succeed("sudo -u alice neuman-admin --json policy show alice")
        )
        assert shown["policy"]["auto_accept"] == "never", shown

    with subtest("a user may choose an approval mode within their ceiling"):
        # Against real Postgres, because the write is an overlay plus an audit
        # append plus a generation bump — the surface tests fake their
        # connection, so a test there would prove the fake agrees with the route.
        # The token is in the login *body*, not a header — the client reads it
        # from the JSON and holds it in memory (never a cookie, so a script
        # injection cannot read it).
        login = json.loads(
            machine.succeed(
                "curl -sS -c /tmp/bob.jar -X POST http://127.0.0.1:8443/api/login "
                "-H 'content-type: application/json' "
                """-d '{"username":"bob","password":"bob-password-123"}'"""
            )
        )
        token = login["csrf_token"]

        # bob was created with a temporary password, so every route except the
        # password change itself answers 403 until he replaces it. This is the
        # first-login gate, against the real server rather than a fake.
        code = machine.succeed(
            "curl -sS -o /tmp/bob-gated.json -w '%{http_code}' -b /tmp/bob.jar -X PUT "
            "http://127.0.0.1:8443/api/policy/auto-accept "
            f"-H 'x-csrf-token: {token}' "
            "-H 'content-type: application/json' "
            "-d '{\"auto_accept\": \"yolo\"}'"
        ).strip()
        assert code == "403", code
        gated = json.loads(machine.succeed("cat /tmp/bob-gated.json"))
        assert gated["error"] == "password_change_required", gated

        out = machine.succeed(
            "curl -sS -b /tmp/bob.jar -X PATCH http://127.0.0.1:8443/api/me/password "
            f"-H 'x-csrf-token: {token}' "
            "-H 'content-type: application/json' "
            "-d '{\"current_password\": \"bob-password-123\", "
            "\"new_password\": \"bob-new-password-123\"}'"
        )
        assert json.loads(out)["ok"] is True, out

        out = machine.succeed(
            "curl -sS -b /tmp/bob.jar -X PUT "
            "http://127.0.0.1:8443/api/policy/auto-accept "
            f"-H 'x-csrf-token: {token}' "
            "-H 'content-type: application/json' "
            """-d '{"auto_accept": "yolo"}'"""
        )
        assert json.loads(out)["auto_accept"] == "yolo", out

        # In force, read back from the server rather than from the response.
        shown = json.loads(machine.succeed("curl -sS -b /tmp/bob.jar \"http://127.0.0.1:8443/api/policy?machine=machine\""))
        assert shown["policy"]["auto_accept"] == "yolo", shown

    with subtest("a mode above the ceiling is refused"):
        # alice is capped at "safe" by the baseline set above. Without this
        # check the feature is a self-granted guardrail removal: scalars are
        # last-wins and the overlay is the last layer, so her own fragment would
        # beat the cap.
        login = json.loads(
            machine.succeed(
                "curl -sS -c /tmp/alice.jar -X POST http://127.0.0.1:8443/api/login "
                "-H 'content-type: application/json' "
                """-d '{"username":"alice","password":"correct horse battery staple"}'"""
            )
        )
        token = login["csrf_token"]

        code = machine.succeed(
            "curl -sS -o /tmp/refused.json -w '%{http_code}' -b /tmp/alice.jar -X PUT "
            "http://127.0.0.1:8443/api/policy/auto-accept "
            f"-H 'x-csrf-token: {token}' "
            "-H 'content-type: application/json' "
            """-d '{"auto_accept": "yolo"}'"""
        ).strip()
        assert code == "403", code
        body = json.loads(machine.succeed("cat /tmp/refused.json"))
        assert body["error"] == "above_ceiling", body

        # And nothing moved. `never` rather than `safe`: an earlier subtest set
        # alice's overlay with the CLI, so the point is that the refused request
        # left whatever was there untouched — not that a particular value is
        # present.
        shown = json.loads(
            machine.succeed(
                "curl -sS -b /tmp/alice.jar "
                "\"http://127.0.0.1:8443/api/policy?machine=machine\""
            )
        )
        assert shown["policy"]["auto_accept"] == "never", shown
        assert shown["policy"]["auto_accept_max"] == "safe", shown

    with subtest("choosing a mode is recorded in the audit chain"):
        # A user lowering their own guardrail is exactly what the chain is for,
        # and the actor is the user because they are who did it.
        out = machine.succeed(
            "sudo -u alice neuman-admin --json audit search "
            "--action policy.auto_accept --limit 10"
        )
        rows = json.loads(out)["hits"]
        assert rows, f"no policy.auto_accept entry in the chain: {out}"
        # bob's successful change is recorded; alice's refusal is not, because
        # nothing happened.
        actors = {row.get("actor") for row in rows}
        assert "bob" in actors, rows
        assert "alice" not in actors, f"a refused change was recorded: {rows}"

    with subtest("a model set through the admin socket reaches the running appliance"):
        # N61 in its current form. `--vision` was once stored and not returned,
        # so a registration that failed looked exactly like one that worked and
        # the only symptom was fs_image refused mid-conversation. The same
        # question applies to a model in the baseline: did what the operator
        # wrote reach what the server resolves?
        #
        # In a VM, because that is the only place the chain is whole — the CLI
        # writes through the socket, Postgres stores it, and the running server
        # resolves from what it swapped in. A unit test can assert any one link.
        listed = json.loads(
            machine.succeed("sudo -u alice neuman-admin --json model list")
        )
        by_name = {m["name"]: m for m in listed["models"]}

        assert by_name["seer"]["vision"] is True, listed
        # Absent means text-only rather than unset, or the module's silence and
        # the server's default could disagree.
        assert by_name["reader"]["vision"] is False, listed

        # Each model resolved to its *own* provider. One global key sent every
        # provider the same bearer token, which is the bug the per-provider
        # credential exists to fix, and a wrong endpoint here would be its
        # visible half.
        assert by_name["seer"]["endpoint"] == "https://seer.example.test/v1", listed
        assert by_name["reader"]["endpoint"] == "https://reader.example.test/v1", listed

        # The key is reported as a path and never as a value: this output goes
        # to an operator's terminal and into whatever they paste it in to.
        providers = {p["name"]: p for p in listed["providers"]}
        assert providers["seer-host"]["api_key_file"] == "/run/neuman-test/seer.key", listed

    with subtest("the session filter searches what was said, in real Postgres"):
        # The surface tests fake the store, so the `tsvector` SQL itself —
        # `plainto_tsquery`, `ts_headline`, the kind filter — is only ever
        # exercised here. A fake that matched the way its author expected proves
        # nothing about what Postgres does (`session_search`).
        #
        # Events are inserted directly rather than by driving a turn: a real
        # turn needs a model, and what is under test is the index rather than
        # the harness. The index is then rebuilt through the store's own
        # `reindex_search`, so the statement this exercises is the shipped one.
        # Written with Python's own `json`/file handling rather than a shell
        # heredoc: a heredoc's body has to sit at column 0 to end its
        # delimiter, and `nixfmt` reindents this whole script, so the two
        # fight and the testScript stops parsing (`check_testscript.py`).
        import shlex

        login = json.loads(
            machine.succeed(
                "curl -sS -c /tmp/search.jar -X POST http://127.0.0.1:8443/api/login "
                "-H 'content-type: application/json' "
                """-d '{"username":"bob","password":"bob-new-password-123"}'"""
            )
        )
        token = login["csrf_token"]

        # bob's computer, recorded the way a client's hello records it. This
        # subtest is about the search SQL, not about enrolment, and a session
        # names the machine it runs on (D112) — the route refuses a label the
        # account has never connected from, which is the rule working and would
        # otherwise make this an assertion about the wrong thing.
        machine.succeed(
            "sudo -u postgres psql -d neuman -c "
            + shlex.quote(
                "INSERT INTO machines (account_id, label) "
                "SELECT id, 'machine' FROM accounts WHERE name = 'bob' "
                "ON CONFLICT DO NOTHING"
            )
        )

        created = json.loads(
            machine.succeed(
                "curl -sS -b /tmp/search.jar -X POST http://127.0.0.1:8443/api/sessions "
                f"-H 'x-csrf-token: {token}' "
                "-H 'content-type: application/json' -d '{\"machine\": \"machine\"}'"
            )
        )
        sid = created["id"]
        bid = created["branch"]["id"]

        rows = ", ".join(
            [
                f"('{sid}', '{bid}', 1, 'user_message', '{json.dumps({'content': 'please raise the retention window'})}')",
                f"('{sid}', '{bid}', 2, 'response_final', '{json.dumps({'message': {'content': 'the ABI is now six'}})}')",
                f"('{sid}', '{bid}', 3, 'tool_result', '{json.dumps({'name': 'fs_read', 'content': 'zebrafish'})}')",
            ]
        )
        machine.succeed(
            "sudo -u postgres psql -d neuman -c "
            + shlex.quote(
                "INSERT INTO session_events (session_id, branch_id, seq, kind, payload) "
                f"VALUES {rows}"
            )
        )

        # Rebuilt by the appliance's own startup reconcile, which is both the
        # shipped statement and the repair path that exists for exactly this
        # divergence: rows reached the log without the inline maintenance
        # running. Restarting is how a VM test reaches a startup job.
        machine.succeed("systemctl restart neuman-server.service")
        machine.wait_for_unit("neuman-server.service")
        machine.wait_for_open_port(8443)
        # The cookie survives a restart (sessions are rows), but prove it rather
        # than assume: a 401 here would make every assertion below vacuous.
        machine.succeed(
            "curl -sS -b /tmp/search.jar http://127.0.0.1:8443/api/sessions "
            "| grep -q sessions"
        )

        def search(query):
            return json.loads(
                machine.succeed(
                    "curl -sS -b /tmp/search.jar "
                    f"'http://127.0.0.1:8443/api/sessions?q={query}'"
                )
            )["sessions"]

        found = search("retention")
        assert [row["id"] for row in found] == [sid], found
        assert "<<retention>>" in found[0]["snippet"], found

        # A prefix of a word finds it, which is what the search box promises
        # (D52) and what `plainto_tsquery` alone did not do: it produced whole
        # lexemes, so `retent` matched nothing.
        found = search("retent")
        assert [row["id"] for row in found] == [sid], found
        assert "<<retention>>" in found[0]["snippet"], found

        # The model's reply is searchable too, not just the user's messages.
        assert [row["id"] for row in search("six")] == [sid], search("six")

        # Tool output is **not** indexed: a filename would otherwise match every
        # session that had ever listed the directory containing it.
        assert search("zebrafish") == [], f"tool output is indexed: {search('zebrafish')}"

        # A task's agent step is the task's, not chat's (D99): the same session
        # marked as a run's leaves the list, search and opening by id — the
        # `task_run_id` scope in each statement, which the fakes only model.
        machine.succeed(
            "sudo -u postgres psql -d neuman -c "
            + shlex.quote(f"UPDATE agent_sessions SET task_run_id = '{'0' * 32}' WHERE id = '{sid}'")
        )
        listed = json.loads(
            machine.succeed("curl -sS -b /tmp/search.jar http://127.0.0.1:8443/api/sessions")
        )["sessions"]
        assert sid not in [row["id"] for row in listed], listed
        assert search("retention") == [], search("retention")
        machine.succeed(
            "curl -sS -o /dev/null -w '%{http_code}' -b /tmp/search.jar "
            f"http://127.0.0.1:8443/api/sessions/{sid} | grep -q 404"
        )

        # The overview's read, over the account-wide run index.
        tasks = json.loads(
            machine.succeed("curl -sS -b /tmp/search.jar http://127.0.0.1:8443/api/tasks")
        )
        assert tasks["tasks"] == [] and tasks["recent"] == [], tasks

    with subtest("the CLI's write is in the audit chain"):
        # Governance events come from core operations (D8), so an overlay set
        # over the socket is recorded the same as one set any other way —
        # and attributed to the uid the kernel reported, not to a name the
        # client chose.
        # A heredoc for the same reason as the fragment above: SQL string
        # literals need single quotes, and `printf` with a `%s` placeholder to
        # smuggle them past three layers of escaping is how the previous
        # version of this file got it wrong.
        # `policy.overlay_set` specifically, not `policy%`: a user choosing their
        # own approval mode is also a `policy.*` event, and it is written by a
        # *user* rather than an operator — so the broader match picked up that
        # row instead and read the attribution of a different operation.
        machine.succeed(
            "cat > /tmp/actor.sql <<'EOF'\n"
            "select actor from audit_chain\n"
            "where action = 'policy.overlay_set'\n"
            "order by seq desc limit 1;\n"
            "EOF"
        )
        actor = machine.succeed(
            "sudo -u postgres psql -d neuman -tAf /tmp/actor.sql"
        ).strip()
        assert actor == "alice", f"the overlay was attributed to {actor!r}"

    with subtest("a backup and a restore make a round trip"):
        # M10's claim, and the one `decision/ops/backup.py` has been making
        # in its own docstring since it was written: *a backup nobody has
        # restored is a hypothesis*. This is the first time a `pg_dump` this
        # appliance produced has been fed to `pg_restore`.
        #
        # The overlay alice set above is the marker. It is destroyed between
        # the backup and the restore, so a restore that copies nothing cannot
        # pass — which is the failure a round-trip test most easily admits.
        machine.succeed("sudo -u alice neuman-admin backup /var/lib/neuman/backups/one")

        manifest = json.loads(
            machine.succeed("cat /var/lib/neuman/backups/one/manifest.json")
        )
        assert "alice.json" in manifest["overlay_files"], manifest
        assert manifest["database_dump"].endswith("neuman.dump"), manifest

        # Destroy both halves: the overlay on disk and a row in the database.
        machine.succeed("rm /var/lib/neuman/state/policies/alice.json")
        machine.succeed(
            "cat > /tmp/wipe.sql <<'EOF'\n"
            "delete from audit_chain;\n"
            "EOF"
        )
        machine.succeed("sudo -u postgres psql -d neuman -f /tmp/wipe.sql")
        wiped = machine.succeed(
            "sudo -u postgres psql -d neuman -tAc 'select count(*) from audit_chain'"
        ).strip()
        assert wiped == "0", f"the chain was not wiped: {wiped!r}"

        restored = json.loads(
            machine.succeed(
                "sudo -u alice neuman-admin --json restore /var/lib/neuman/backups/one"
            )
        )
        assert restored["database_loaded"] is True, restored
        assert "alice.json" in restored["overlay_files"], restored
        # Verification is part of the restore, not a later step: a restore
        # that loaded cleanly but produced a broken chain is not a success.
        assert restored["chain_ok"] is True, restored

        # The overlay is back on disk...
        overlay = json.loads(
            machine.succeed("cat /var/lib/neuman/state/policies/alice.json")
        )
        assert overlay["auto_accept"] == "never", overlay

        # ...and the rows are back in the database, which the manifest alone
        # would not have shown.
        rows = int(machine.succeed(
            "sudo -u postgres psql -d neuman -tAc 'select count(*) from audit_chain'"
        ).strip())
        assert rows > 0, "the audit chain did not come back"

    with subtest("a directory that is not a backup is refused"):
        # And refused *before* anything is written, which is what makes a
        # failed restore recoverable rather than a third state.
        machine.succeed("mkdir -p /var/lib/neuman/backups/empty")
        code, out = machine.execute(
            "sudo -u alice neuman-admin restore /var/lib/neuman/backups/empty 2>&1"
        )
        assert code == 1, f"expected a refusal, got exit {code}: {out}"
        assert "manifest" in out, out
        # The live overlay is untouched.
        machine.succeed("test -f /var/lib/neuman/state/policies/alice.json")

    with subtest("a non-operator cannot restore"):
        # The most destructive operation on the appliance, and the guard is
        # the same one every admin route gets (N32).
        code, out = machine.execute(
            "sudo -u carol neuman-admin restore /var/lib/neuman/backups/one 2>&1"
        )
        assert code == 1, f"carol got exit {code}: {out}"
        assert "not an admin" in out, out

    with subtest("a real login works end to end"):
        # Through the module, the account row the bootstrap created, and the
        # server it wired — the first time all three are the appliance's own
        # rather than a test's.
        out = machine.succeed(
            "curl -sS -i -X POST http://127.0.0.1:8443/api/login "
            "-H 'content-type: application/json' "
            """-d '{"username":"alice","password":"correct horse battery staple"}'"""
        )
        assert "200 OK" in out, out
        assert "HttpOnly" in out, f"the session cookie must be HttpOnly: {out}"

        # And the account was seen. `accounts.last_seen` was once written when
        # the row was made and never again, so the roster's LAST SEEN was the
        # creation date; a login moves it, in the statement that makes the
        # session. SQL, so only a real Postgres can say.
        moved = machine.succeed(
            "sudo -u postgres psql -d neuman -tAc "
            "\"select last_seen > first_seen from accounts where name = 'alice'\""
        ).strip()
        assert moved == "t", f"a login did not move last_seen: {moved!r}"

    with subtest("a wrong password is refused"):
        out = machine.succeed(
            "curl -sS -o /dev/null -w '%{http_code}' -X POST "
            "http://127.0.0.1:8443/api/login "
            "-H 'content-type: application/json' "
            """-d '{"username":"alice","password":"wrong"}'"""
        ).strip()
        assert out == "401", out

    with subtest("the server runs unprivileged"):
        # Never root, and no path to the host's password file. There is no root
        # component left whose privilege this could be borrowing.
        user = machine.succeed(
            "systemctl show -p User neuman-server.service"
        ).strip()
        assert user == "User=neuman", user
        machine.fail("sudo -u neuman cat /etc/shadow")

    with subtest("state lands where the module said it would"):
        for path in (
            "/var/lib/neuman/state",
            "/var/lib/neuman/state/policies",
            "/var/log/neuman",
        ):
            owner = machine.succeed(f"stat -c %U {path}").strip()
            assert owner == "neuman", f"{path} is owned by {owner}"

        # Where a key entered in the administration page is written (D77). Its
        # mode is the access control, so it is asserted rather than assumed.
        secrets = machine.succeed("stat -c '%U %a' /var/lib/neuman/state/secrets").split()
        assert secrets == ["neuman", "700"], secrets

    with subtest("the audit chain recorded the login"):
        # Governance events are emitted by core operations, never by a
        # transport surface (D8) — so a successful login is in the chain
        # whether or not anything asked it to be.
        #
        # The query goes through a file: nesting shell quoting inside Nix
        # inside Python is three layers of escaping and one of them is always
        # wrong.
        machine.succeed(
            "printf 'select count(*) from audit_chain where action = %s;' "
            "\"'identity.login'\" > /tmp/audit.sql"
        )
        rows = machine.succeed(
            "sudo -u postgres psql -d neuman -tAf /tmp/audit.sql"
        ).strip()
        assert int(rows) >= 1, f"no login in the audit chain: {rows!r}"
  '';
}
