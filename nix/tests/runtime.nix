# A model on this machine, end to end: fetched, verified, added, served (D78, D79).
#
# `test_runtime.py` runs the host and the fetcher against stand-in programs.
# This runs the real ones: `llama-server` in router mode on the CPU with a real
# GGUF, `neuman-fetch` downloading it over HTTP into the real `modelsDir`, and
# the server's own resolver sending a completion to the router. What only a
# booted machine can say is here — which unit can write the weights, which holds
# the device, and whether the router serves anything a preset did not name.
#
# The VM has no network, so the "Hub" is a mirror on loopback serving a tiny
# model fetched at build time, and the server package is the real one with one
# preset added for it: the shipped presets are 1–17 GB each.
{ self, pkgs }:

let
  packages = self.packages.${pkgs.stdenv.hostPlatform.system};

  revision = "499bc8821c6b12b4e53c5bffcb21ec206f212d81";

  # 1.2 MB of llama weights trained on children's stories: the smallest GGUF
  # llama.cpp's own CI runs, and enough for a real completion on a CPU.
  tiny = pkgs.fetchurl {
    url = "https://huggingface.co/ggml-org/models-moved/resolve/${revision}/tinyllamas/stories260K.gguf";
    sha256 = "270cba1bd5109f42d03350f60406024560464db173c0e387d91f0426d3bd256d";
  };

  # Laid out the way the Hub serves a file: `<repo>/resolve/<commit>/<name>`.
  hub = pkgs.linkFarm "hub" [
    {
      name = "test/tiny/resolve/${revision}/stories260K.gguf";
      path = tiny;
    }
  ];

  preset = pkgs.writeText "tiny.json" (
    builtins.toJSON {
      name = "tiny";
      label = "Tiny stories";
      local = {
        verified = "the VM test's own mirror";
        facts = {
          weights = {
            repo = "test/tiny";
            inherit revision;
            files = [
              {
                name = "stories260K.gguf";
                size = 1185376;
                sha256 = "270cba1bd5109f42d03350f60406024560464db173c0e387d91f0426d3bd256d";
                role = "model";
              }
            ];
          };
        };
        policy = {
          context_size = 512;
          max_tokens = 16;
          gpu_layers = 0;
          residency = "on-demand";
          idle_minutes = 1;
        };
      };
    }
  );

  # The real server with one more preset, which is how a preset ships: a file
  # in the package, read by the server, the host and the fetcher alike.
  server = packages.neuman-server.overridePythonAttrs (old: {
    postInstall = (old.postInstall or "") + ''
      cp ${preset} $out/${pkgs.python312.sitePackages}/neuman_server/presets/models/tiny.json
    '';
  });

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
in
pkgs.testers.nixosTest {
  name = "neuman-runtime";

  nodes.machine =
    { ... }:
    {
      imports = [ self.nixosModules.neuman ];

      users.users.alice.isNormalUser = true;
      services.neuman = {
        enable = true;
        package = server;
        gpu = "none";
        adminSocketAccess = [ "alice" ];
        fetch.baseUrl = "http://127.0.0.1:8000";
      };

      systemd.services.hub = {
        description = "A stand-in for the Hugging Face Hub";
        wantedBy = [ "multi-user.target" ];
        serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 -m http.server --bind 127.0.0.1 --directory ${hub} 8000";
      };

      environment.systemPackages = [
        gatewayProbe
        pkgs.curl
        pkgs.util-linux
      ];

      virtualisation.memorySize = 2048;
      # The fetcher leaves 1 GiB free beside a download; the default disk has
      # less than that to begin with.
      virtualisation.diskSize = 4096;
    };

  testScript = ''
    import json
    import time

    machine.wait_for_unit("multi-user.target")
    for unit in ("neuman-server", "neuman-runtime", "neuman-fetch", "hub"):
        machine.wait_for_unit(f"{unit}.service")
    machine.wait_for_open_port(8443)
    machine.wait_for_open_port(8000)

    def admin(method, path, body=None, ok=True):
        data = f"--data-binary '{json.dumps(body)}'" if body is not None else ""
        fail = "--fail-with-body" if ok else "--show-error"
        out = machine.succeed(
            f"runuser -u alice -- curl -sS {fail} "
            f"--unix-socket /run/neuman/admin/admin.sock -X {method} "
            f"-H 'content-type: application/json' {data} http://admin{path}"
        )
        return json.loads(out) if out.strip() else None

    def control(method, path, body=None):
        data = f"--data-binary '{json.dumps(body)}'" if body is not None else ""
        out = machine.succeed(
            f"curl -sS --unix-socket /run/neuman/runtime/control.sock -X {method} "
            f"-H 'content-type: application/json' {data} http://runtime{path}"
        )
        return json.loads(out)

    def tile():
        return next(t for t in admin("GET", "/admin/catalog")["models"] if t["id"] == "tiny")["local"]

    def until(what, check, limit=120):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(1)
        raise AssertionError(f"timed out waiting for {what}")

    def as_unit(unit, command):
        """Run `command` as neuman inside `unit`'s mount namespace: its own view
        of what is writable, not the test's."""
        pid = machine.succeed(f"systemctl show -p MainPID --value {unit}").strip()
        uid = machine.succeed("id -u neuman").strip()
        gid = machine.succeed("id -g neuman").strip()
        return machine.execute(
            f"nsenter --target {pid} --mount --setuid {uid} --setgid {gid} -- {command}"
        )

    with subtest("an operator exists and the weights are absent"):
        machine.succeed(
            "printf %s 'correct horse battery' > /tmp/pw && chown alice /tmp/pw && chmod 600 /tmp/pw"
        )
        machine.succeed("runuser -u alice -- neuman-admin bootstrap --name alice --password-file /tmp/pw")
        assert tile()["weights"]["state"] == "absent", tile()

    with subtest("adding a model before its weights are verified is refused"):
        refused = admin("POST", "/admin/models/local", {"preset": "tiny"}, ok=False)
        assert refused["error"] == "weights_not_verified", refused

    with subtest("somebody else's file under the pinned name is refused, not overwritten"):
        machine.succeed(
            "printf 'not the weights' > /var/lib/neuman/models/stories260K.gguf && "
            "chown neuman:neuman /var/lib/neuman/models/stories260K.gguf"
        )
        admin("POST", "/admin/weights/tiny/fetch", {})
        job = until("the fetch to end", lambda: (tile()["fetch"] or {}).get("state") in ("failed", "done") and tile()["fetch"])
        assert job["state"] == "failed" and "another size" in job["error"], job
        assert machine.succeed("cat /var/lib/neuman/models/stories260K.gguf") == "not the weights"
        machine.succeed("rm /var/lib/neuman/models/stories260K.gguf")

    with subtest("the weights are fetched from the pinned commit and verified"):
        admin("POST", "/admin/weights/tiny/fetch", {})
        until("verified weights", lambda: tile()["weights"]["state"] == "verified")
        machine.succeed("cmp /var/lib/neuman/models/stories260K.gguf ${tiny}")
        machine.succeed("test -e /var/lib/neuman/models/.verified/stories260K.gguf.json")
        machine.fail("test -e /var/lib/neuman/models/stories260K.gguf.part")

    with subtest("only the fetcher can write the weights"):
        # The server could otherwise choose what the GPU runs by replacing a file;
        # the runtime has no reason to write what it loads.
        for unit in ("neuman-server", "neuman-runtime"):
            code, out = as_unit(unit, "touch /var/lib/neuman/models/probe")
            assert code != 0, f"{unit} could write the weights: {out}"
        code, out = as_unit("neuman-fetch", "touch /var/lib/neuman/models/probe")
        assert code == 0, f"the fetcher could not write the weights: {out}"
        machine.succeed("rm /var/lib/neuman/models/probe")

    with subtest("only the runtime holds devices, and only the fetcher reaches out"):
        show = lambda unit, prop: machine.succeed(f"systemctl show -p {prop} --value {unit}").strip()
        assert show("neuman-runtime", "PrivateDevices") == "no"
        assert show("neuman-server", "PrivateDevices") == "yes"
        assert show("neuman-fetch", "PrivateDevices") == "yes"
        # The runtime may reach loopback and nothing else.
        assert "0.0.0.0/0" in show("neuman-runtime", "IPAddressDeny") or show("neuman-runtime", "IPAddressDeny") == "any", show("neuman-runtime", "IPAddressDeny")

    with subtest("adding the model tells the runtime host, which serves it"):
        added = admin("POST", "/admin/models/local", {"preset": "tiny"})
        assert added["model"]["name"] == "tiny", added
        document = json.loads(machine.succeed("cat /var/lib/neuman/state/runtime.json"))
        assert document["chat"][0]["preset"] == "tiny", document
        until("the host to run it", lambda: [m["name"] for m in control("GET", "/status")["chat"]] == ["tiny"])
        machine.wait_for_open_port(8081)

        # Only what a preset named: no model from a cache, no download argument.
        listed = json.loads(machine.succeed("curl -sS http://127.0.0.1:8081/models"))
        assert [m["id"] for m in listed["data"]] == ["tiny"], listed

    with subtest("a completion goes through the server's resolver to the router"):
        code, out = machine.execute(
            "runuser -u neuman -- env $(systemctl show -p Environment --value "
            "neuman-server.service) ${gatewayProbe}/bin/neuman-gateway-probe tiny 2>&1"
        )
        assert code == 0, f"the probe exited {code}: {out}"
        assert "ENDPOINT http://127.0.0.1:8081/v1" in out, out
        assert "FINISH" in out and "ERROR" not in out, out
        assert control("GET", "/status")["chat"][0]["loaded"] is True

    with subtest("unloading frees it, and a name the host was not told is nothing"):
        control("POST", "/unload", {"name": "tiny"})
        assert control("GET", "/status")["chat"][0]["loaded"] is False
        code, _ = machine.execute(
            "curl -sS --fail --unix-socket /run/neuman/runtime/control.sock -X POST "
            "-d '{\"name\":\"neuman-server\"}' http://runtime/load"
        )
        assert code != 0
  '';
}
