# The gateway seam: the real client, a real socket, a real key file.
#
# `test_gateway.py` drives `ModelClient` over an in-process ASGI transport,
# which covers the wire format and the framing. What it cannot cover is the
# part that was actually broken: the key travelling from where an operator put
# it, into an Authorization header on a request leaving a real process.
#
# The key is put where an operator puts it now: pasted into the administration
# surface, which writes it to a file only `neuman` can read (D77). So this is
# also the test of that claim on a real machine — the file's owner and mode,
# and the key's absence from the database, the audit mirror, a backup and the
# unit's environment.
#
# That is where N38 lived. `build_services` passed the *environment* variable
# while the module only ever sets the *file* path — a secret in a unit's
# environment is readable through /proc and the store — so every model request
# from a real appliance went out unauthenticated, and the function written to
# read the file had no callers at all. Both halves were individually sensible
# and nothing joined them up.
#
# The stub records what it receives, so the assertions are about what arrived
# rather than about what the client believes it sent.
{ self, pkgs }:

let
  packages = self.packages.${pkgs.stdenv.hostPlatform.system};

  # An OpenAI-compatible endpoint, as small as the contract allows. It writes
  # every request's Authorization header and body to a file the test reads —
  # a stub that only answered correctly would prove nothing about the key.
  stubModel = pkgs.writers.writePython3Bin "stub-model" { flakeIgnore = [ "E501" ]; } ''
    import json
    import os
    from http.server import BaseHTTPRequestHandler, HTTPServer

    RECORD = "/run/stub-model/requests.jsonl"

    STREAM = [
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"content": "hello from the stub"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}, "choices": []},
    ]


    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else ""

            os.makedirs(os.path.dirname(RECORD), exist_ok=True)
            with open(RECORD, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "path": self.path,
                    "authorization": self.headers.get("authorization"),
                    "accept": self.headers.get("accept"),
                    "body": body,
                }) + "\n")

            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for event in STREAM:
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *args):
            pass


    HTTPServer(("127.0.0.1", 9099), Handler).serve_forever()
  '';

  # Drives the real ModelClient from inside the guest, the same way
  # `client_probe.py` logs in and connects: the production path,
  # not a reimplementation of the protocol.
  gatewayProbe = pkgs.writeShellScriptBin "neuman-gateway-probe" ''
    export PYTHONPATH="${self}/neuman-server/src:${self}/contracts/gen/python"
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
    }/bin/python3 ${./gateway_probe.py} "$@"
  '';

  # The request that adds the provider, with the key in its body — a file
  # because a key on a command line is in `/proc/<pid>/cmdline` for as long as
  # the command runs. The trailing newline is the one every paste and every
  # secrets manager adds, and the server must strip it.
  addProvider = pkgs.writeText "add-provider.json" (
    builtins.toJSON {
      name = "stub-host";
      url = "http://127.0.0.1:9099/v1";
      api_key = "sk-test-key-from-the-panel\n";
    }
  );
in
pkgs.testers.nixosTest {
  name = "neuman-gateway";

  nodes.machine =
    { ... }:
    {
      imports = [ self.nixosModules.neuman ];

      # The operator, and the only configuration the module carries: who may
      # reach the admin socket. Everything else is made through it.
      users.users.alice.isNormalUser = true;
      services.neuman = {
        enable = true;
        # The runtime host is on every appliance; a VM has no GPU, and the CUDA
        # build is unfree and gigabytes.
        gpu = "none";
        adminSocketAccess = [ "alice" ];
      };

      systemd.services.stub-model = {
        description = "An OpenAI-compatible stub";
        wantedBy = [ "multi-user.target" ];
        before = [ "neuman-server.service" ];
        serviceConfig = {
          ExecStart = "${stubModel}/bin/stub-model";
          RuntimeDirectory = "stub-model";
          # World-readable so the test can read the record without sudo, and
          # because nothing secret is in it — that is the point of asserting on
          # what the stub *received*.
          RuntimeDirectoryMode = "0755";
        };
      };

      environment.systemPackages = [
        gatewayProbe
        pkgs.curl
      ];

      virtualisation.memorySize = 2048;
    };

  testScript = ''
    import json

    machine.wait_for_unit("multi-user.target")
    machine.wait_for_unit("stub-model.service")
    machine.wait_for_open_port(9099)

    def admin(method, path, body=None):
        """A request to the admin socket as alice, the way the panel's routes are reached."""

        data = f"--data-binary @{body}" if body else ""
        out = machine.succeed(
            f"runuser -u alice -- curl -sS --fail-with-body --unix-socket "
            f"/run/neuman/admin/admin.sock -X {method} -H 'content-type: application/json' "
            f"{data} http://admin{path}"
        )
        return json.loads(out) if out.strip() else None

    with subtest("a fresh appliance starts empty and takes a provider and its key"):
        machine.wait_for_unit("neuman-server.service")
        machine.wait_for_open_port(8443)
        machine.succeed(
            "printf %s 'correct horse battery' > /tmp/pw && chown alice /tmp/pw && chmod 600 /tmp/pw"
        )
        machine.succeed(
            "runuser -u alice -- neuman-admin bootstrap --name alice --password-file /tmp/pw"
        )

        # No seed: the module writes no baseline, so the database row is the
        # empty document the server made on first start.
        machine.fail("test -e /etc/neuman/baseline.json")
        baseline = admin("GET", "/admin/baseline")["baseline"]
        assert not baseline.get("providers") and not baseline.get("models"), baseline

        added = admin("POST", "/admin/providers", "${addProvider}")
        key_file = added["provider"]["api_key_file"]
        assert key_file == "/var/lib/neuman/state/secrets/stub-host.key", added

        # A model Neuman ships no preset for is declared by hand, which is the
        # custom path the administration page keeps.
        baseline = admin("GET", "/admin/baseline")["baseline"]
        baseline.setdefault("models", {})["stub"] = {
            "provider": "stub-host",
            "provider_model": "stub",
        }
        machine.succeed(
            "cat > /tmp/baseline.json <<'DOC'\n" + json.dumps({"baseline": baseline}) + "\nDOC"
        )
        admin("PUT", "/admin/baseline", "/tmp/baseline.json")

    with subtest("the key is a file only neuman can read"):
        owner, mode = machine.succeed(f"stat -c '%U %a' {key_file}").split()
        assert (owner, mode) == ("neuman", "600"), (owner, mode)
        directory = machine.succeed("stat -c '%U %a' /var/lib/neuman/state/secrets").split()
        assert directory == ["neuman", "700"], directory
        machine.fail(f"runuser -u alice -- cat {key_file}")

    with subtest("a real request carries the key from the file"):
        # The assertion N38 needed. `build_services` read the environment
        # variable; the module only sets the file path. Nothing joined them, so
        # every request went out unauthenticated — and no test looked at a
        # request that had actually left the process.
        # With the *server unit's own* environment, read from systemd rather
        # than restated here. The probe runs as a separate process, so without
        # this it inherits nothing and `Settings.from_env` silently falls back
        # to defaults — which is a test of the defaults, not of the module.
        # Reading it from `systemctl show` also means the test cannot disagree
        # with the unit about what was configured.
        # By absolute store path: the unit's Environment replaces PATH along
        # with everything else, so a bare name is not found.
        # As `neuman`, which is who reads the key and who may open the database.
        code, out = machine.execute(
            "runuser -u neuman -- env $(systemctl show -p Environment --value "
            "neuman-server.service) ${gatewayProbe}/bin/neuman-gateway-probe 2>&1"
        )
        assert code == 0, f"the probe exited {code}: {out}"
        machine.succeed(f"cat > /tmp/probe.out <<'PROBE'\n{out}\nPROBE")
        assert "hello from the stub" in out, out

        recorded = [
            json.loads(line)
            for line in machine.succeed("cat /run/stub-model/requests.jsonl").splitlines()
            if line.strip()
        ]
        assert recorded, "the stub received no request"

        last = recorded[-1]
        assert last["authorization"] == "Bearer sk-test-key-from-the-panel", (
            f"the key did not arrive: {last['authorization']!r}"
        )

    with subtest("the key arrives without the paste's trailing newline"):
        # A pasted key and every secrets manager end with one. A newline inside
        # a Bearer token is a 401 from the provider, which reads as a wrong key
        # rather than a whitespace bug.
        last = json.loads(
            machine.succeed("tail -n 1 /run/stub-model/requests.jsonl")
        )
        assert "\n" not in last["authorization"], repr(last["authorization"])
        assert last["authorization"].endswith("panel"), repr(last["authorization"])

    with subtest("the request asks for a stream and for usage"):
        # Without `include_usage` the usage event never arrives and budget
        # accounting silently records nothing (D20 keeps accounting in our own
        # tables, so there is nothing else to fall back on).
        last = json.loads(
            machine.succeed("tail -n 1 /run/stub-model/requests.jsonl")
        )
        body = json.loads(last["body"])
        assert body["stream"] is True, body
        assert body["stream_options"] == {"include_usage": True}, body
        assert last["accept"] == "text/event-stream", last

    with subtest("the URL is the configured endpoint plus the completions path"):
        last = json.loads(
            machine.succeed("tail -n 1 /run/stub-model/requests.jsonl")
        )
        assert last["path"] == "/v1/chat/completions", last["path"]

    with subtest("the key is in its file and nowhere else"):
        # D77's claim, checked where it could fail: the unit's environment
        # (readable through /proc), the database and therefore every dump of
        # it, the audit mirror, and a backup an operator might copy anywhere.
        key = "sk-test-key-from-the-panel"
        environ = machine.succeed(
            "tr '\\0' '\\n' < /proc/$(systemctl show -p MainPID --value "
            "neuman-server.service)/environ"
        )
        assert key not in environ, "the provider key is in the unit's environment"

        # Each search is paired with one for the key's *path*, which must be
        # found: a grep over the wrong text, or over compressed bytes, finds no
        # key either, and would pass for the wrong reason.
        dump = machine.succeed("runuser -u postgres -- pg_dump neuman")
        assert key_file in dump, "the dump does not hold the catalog; the search proves nothing"
        assert key not in dump, "the provider key is in the database"

        audit = machine.succeed("cat /var/log/neuman/audit.jsonl")
        assert "settings.provider_added" in audit, audit
        assert key not in audit, "the provider key is in the audit mirror"

        # A backup's dump is pg_dump's custom format, which is compressed, so it
        # is read back as SQL before it is searched.
        machine.succeed("runuser -u alice -- neuman-admin backup /var/lib/neuman/backups/check")
        restored = machine.succeed(
            "pg_restore -f - /var/lib/neuman/backups/check/neuman.dump; "
            "cat /var/lib/neuman/backups/check/*.json 2>/dev/null || true"
        )
        assert key_file in restored, "the backup does not hold the catalog"
        assert key not in restored, "the provider key is in a backup"
        machine.succeed(f"! grep -rqa {key} /var/lib/neuman/backups/check")

    with subtest("a usage event reaches the client"):
        out = machine.succeed("cat /tmp/probe.out")
        assert "USAGE total_tokens=11" in out, out
  '';
}
