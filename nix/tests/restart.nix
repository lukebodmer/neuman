# Restart: the appliance comes back, and comes back with its state.
#
# Every other test boots into a fresh appliance and asserts it works. That is
# the case an appliance is in exactly once. The normal case is the one nothing
# covered: a machine that already has data, restarting — after an upgrade, a
# power loss, or an operator running `systemctl restart`.
#
# What only a restart can catch: migrations running against a *populated*
# database rather than an empty one, state that lives in a tmpfs and silently
# vanishes, a socket file left behind that makes the next bind fail, and units
# whose ordering happens to work when everything starts at once. All of those
# look fine on a first boot.
#
# Deliberately separate from `vm-appliance` rather than another subtest in it:
# a restart takes the machine down, and a failure after that point would be
# indistinguishable from a failure in whatever ran before. Keeping it apart
# means a break here points at restart rather than at the appliance.
{ self, pkgs }:

let
  # The restart's configuration, made the way an operator makes it: through
  # the admin socket after the first administrator exists.
  baseline = pkgs.writeText "baseline.json" (
    builtins.toJSON {
      schema_version = 2;
      defaults = { };
      roles.engineer.tools = [ "fs_read" ];
      users = [
        {
          name = "alice";
          roles = [ "engineer" ];
        }
      ];
    }
  );

  addProvider = pkgs.writeText "add-provider.json" (
    builtins.toJSON {
      name = "lab";
      url = "https://lab.invalid/v1";
      api_key = "sk-restart-survivor";
    }
  );
in
pkgs.testers.nixosTest {
  name = "neuman-restart";

  nodes.machine =
    { ... }:
    {
      imports = [ self.nixosModules.neuman ];

      users.users.alice = {
        isNormalUser = true;
      };

      services.neuman = {
        enable = true;
        # The runtime host is on every appliance; a VM has no GPU, and the CUDA
        # build is unfree and gigabytes.
        gpu = "none";
        adminSocketAccess = [ "alice" ];
      };

      environment.systemPackages = [ pkgs.curl ];

      virtualisation.memorySize = 2048;
    };

  testScript = ''
    import json

    machine.wait_for_unit("multi-user.target")
    machine.wait_for_unit("neuman-server.service")
    machine.wait_for_open_port(8443)
    machine.wait_until_succeeds("test -S /run/neuman/admin/admin.sock")
    machine.succeed(
        "printf '%s' 'correct horse battery staple' > /tmp/alice.pw && chown alice /tmp/alice.pw && chmod 600 /tmp/alice.pw"
    )
    machine.succeed(
        "sudo -u alice neuman-admin bootstrap --name alice --password-file /tmp/alice.pw"
    )
    machine.succeed("sudo -u alice neuman-admin baseline set ${baseline}")
    key_file = json.loads(
        machine.succeed(
            "sudo -u alice curl -sS --fail-with-body --unix-socket /run/neuman/admin/admin.sock "
            "-X POST -H 'content-type: application/json' --data-binary @${addProvider} "
            "http://admin/admin/providers"
        )
    )["provider"]["api_key_file"]

    def login(password="correct horse battery staple"):
        """A real login, which writes a session row and an audit entry."""

        return machine.succeed(
            "curl -sS -o /dev/null -w '%{http_code}' -X POST "
            "http://127.0.0.1:8443/api/login "
            "-H 'content-type: application/json' "
            f"""-d '{{"username":"alice","password":"{password}"}}'"""
        ).strip()

    def count(table):
        return int(machine.succeed(
            f"sudo -u postgres psql -d neuman -tAc 'select count(*) from {table}'"
        ).strip())

    with subtest("state exists before the restart"):
        # Three kinds, because they survive by three different mechanisms:
        # database rows, a file the server writes, and the audit mirror.
        assert login() == "200"

        machine.succeed(
            "cat > /tmp/frag.json <<'EOF'\n"
            '{"auto_accept": "never"}\n'
            "EOF"
        )
        machine.succeed("chmod 0644 /tmp/frag.json")
        machine.succeed("sudo -u alice neuman-admin policy set alice /tmp/frag.json")

        before = {
            "audit": count("audit_chain"),
            "sessions": count("auth_sessions"),
            "schema": machine.succeed(
                "sudo -u postgres psql -d neuman -tAc "
                "'select max(version) from schema_migrations'"
            ).strip(),
        }
        assert before["audit"] > 0, before
        assert before["sessions"] > 0, before
        machine.succeed("test -f /var/lib/neuman/state/policies/alice.json")
        machine.succeed(f"test -f {key_file}")

    with subtest("the service restarts cleanly"):
        # `systemctl restart` rather than a reboot first: it isolates the
        # unit's own startup from the whole machine's, so a failure here is
        # the server and not the boot order.
        machine.succeed("systemctl restart neuman-server.service")
        machine.wait_for_unit("neuman-server.service")
        machine.wait_for_open_port(8443)

        # The admin socket has to come back too. A socket file left behind by
        # the previous process would make the bind fail with EADDRINUSE, and
        # the server would restart-loop instead of starting.
        machine.wait_until_succeeds("test -S /run/neuman/admin/admin.sock")

    with subtest("nothing was lost to the restart"):
        assert count("audit_chain") >= before["audit"], "audit entries vanished"
        assert count("auth_sessions") >= before["sessions"], "login sessions vanished"
        machine.succeed("test -f /var/lib/neuman/state/policies/alice.json")

    with subtest("migrations do not re-run against a populated database"):
        # The lifespan migrates on every start. Against an empty database that
        # is obviously right; against a populated one it must be a no-op, and
        # a migration that is not idempotent would either fail loudly or
        # duplicate data quietly. The version is the cheap check.
        after = machine.succeed(
            "sudo -u postgres psql -d neuman -tAc "
            "'select max(version) from schema_migrations'"
        ).strip()
        assert after == before["schema"], (
            f"schema version moved from {before['schema']} to {after} on a restart"
        )

        applied = count("schema_migrations")
        machine.succeed("systemctl restart neuman-server.service")
        machine.wait_for_open_port(8443)
        assert count("schema_migrations") == applied, (
            "a migration was recorded twice; migrations are not idempotent"
        )

    with subtest("the appliance still works after restarting"):
        # Liveness is not the same as working. A server that answers /healthz
        # with a broken pool would pass every check above.
        assert login() == "200"
        assert login("wrong") == "401"
        machine.succeed("sudo -u alice neuman-admin --json status")

    with subtest("the audit chain is still intact across the restart"):
        # The strongest available statement that nothing was corrupted: the
        # hashes are recomputed over every row, so a truncated write or a
        # reordered insert during shutdown would show up here and nowhere
        # else.
        out = machine.succeed("sudo -u alice neuman-admin --json audit verify")
        verification = json.loads(out)
        assert verification.get("ok") is True, verification

    with subtest("a full reboot brings the appliance back"):
        # The restart above exercised the unit. This exercises the machine:
        # directories recreated, Postgres and its setup coming up before the server,
        # /run repopulated from nothing, and units ordered by dependency
        # rather than by whatever happened to already be running.
        machine.shutdown()
        machine.start()
        machine.wait_for_unit("multi-user.target")
        machine.wait_for_unit("neuman-server.service")
        machine.wait_for_open_port(8443)

        assert count("audit_chain") >= before["audit"], "a reboot lost audit entries"
        machine.succeed("test -f /var/lib/neuman/state/policies/alice.json")
        assert login() == "200"

    with subtest("a key entered before the reboot is still the provider's after it"):
        # The key is a file under /var/lib, beside the overlays, and the baseline
        # names it. Both have to come back, and the sweep that runs after every
        # baseline write must not mistake a key the database still names for an
        # orphan.
        assert machine.succeed(f"cat {key_file}").strip() == "sk-restart-survivor"
        assert machine.succeed(f"stat -c '%U %a' {key_file}").split() == ["neuman", "600"]
        document = json.loads(
            machine.succeed("sudo -u alice neuman-admin --json baseline show")
        )["baseline"]
        assert document["providers"]["lab"]["api_key_file"] == key_file, document

    with subtest("the runtime directories are rebuilt on a cold boot"):
        # /run is a tmpfs, so everything in it is gone. The unit's
        # `RuntimeDirectory` is what puts the admin directory back, and its mode
        # is load-bearing: it keeps non-admins from reaching the socket at all,
        # and the socket's own mode is the first check.
        machine.wait_until_succeeds("test -S /run/neuman/admin/admin.sock")

        assert machine.succeed("stat -c %a /run/neuman/admin").strip() == "750"
        assert machine.succeed("stat -c %a /run/neuman/admin/admin.sock").strip() == "660"
        assert (
            machine.succeed("stat -c %U:%G /run/neuman/admin/admin.sock").strip()
            == "neuman:neuman"
        )

    with subtest("starting over is delete and restart, with no rebuild"):
        # `docs/setup.md`'s "Starting over", run as written. The first version
        # of it deleted the directories and relied on a rebuild to put them
        # back; tmpfiles only re-runs when its rules change, so the unit could
        # not set up its sandbox and failed with 226/NAMESPACE, and the dropped
        # database was not recreated because `postgresql-setup` had already run
        # (N167). The unit now makes its own directories, so this is the whole
        # procedure.
        machine.succeed("systemctl stop neuman-server")
        machine.succeed("sudo -u postgres dropdb neuman")
        machine.succeed(
            "rm -rf /var/lib/neuman/state /var/lib/neuman/spill /var/log/neuman"
        )
        machine.succeed("systemctl restart postgresql-setup")
        machine.succeed("systemctl start neuman-server")
        machine.wait_for_unit("neuman-server.service")
        machine.wait_until_succeeds("test -S /run/neuman/admin/admin.sock")

        # Everything the wipe removed is back, with the modes that are the
        # access control.
        for path, expected in (
            ("/var/lib/neuman", "neuman 750"),
            ("/var/lib/neuman/state", "neuman 750"),
            ("/var/lib/neuman/state/policies", "neuman 750"),
            ("/var/lib/neuman/state/secrets", "neuman 700"),
            ("/var/log/neuman", "neuman 750"),
            ("/run/neuman/admin", "neuman 750"),
        ):
            got = machine.succeed(f"stat -c '%U %a' {path}").strip()
            assert got == expected, f"{path}: {got}, expected {expected}"

        # And it is a fresh appliance: an empty baseline, no administrator, and
        # the bootstrap that a fresh appliance accepts exactly once.
        stored = json.loads(
            machine.succeed(
                "sudo -u postgres psql -d neuman -tAc "
                "\"select value from settings where key = 'baseline'\""
            )
        )
        assert stored == {"schema_version": 2, "defaults": {}, "roles": {}, "users": []}, stored
        machine.succeed(
            "sudo -u alice neuman-admin bootstrap --name alice --password-file /tmp/alice.pw"
        )
        assert login() == "200"
  '';
}
