{
  description = "Neuman — a governed agent appliance";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  # The CUDA cache, so building Neuman's model runtimes is a download rather than a
  # discovery.
  #
  # `services.neuman.gpu` defaults to `cuda`, which means the runtimes are CUDA
  # builds — and nixpkgs' own cache does not carry those, because CUDA is unfree.
  # Without this substituter the first build compiles a CUDA toolchain from source,
  # which is the cost that made a CPU default look reasonable — and that default left
  # local models sixty times slower (N98). With it, the CUDA build is a download.
  #
  # Note the host: the cache moved from `cuda-maintainers.cachix.org` to
  # `cache.nixos-cuda.org` in November 2025, and the old name now answers 401 rather
  # than redirecting — so a stale copy of this setting fails as "not authorized"
  # rather than as "not found".
  #
  # `extra-` rather than replacing, so this adds to whatever the machine already
  # trusts instead of overriding it. A flake's `nixConfig` still has to be accepted
  # by the user the first time it is seen, or set system-wide in `nix.settings`.
  nixConfig = {
    extra-substituters = [ "https://cache.nixos-cuda.org" ];
    extra-trusted-public-keys = [
      "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M="
    ];
  };

  outputs =
    { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor = system: import nixpkgs { inherit system; };

      # The contracts part needs only a validator and pytest.
      pyEnv =
        pkgs:
        pkgs.python312.withPackages (ps: [
          ps.jsonschema
          ps.pytest
        ]);

      # The server's own environment.
      serverEnv =
        pkgs:
        pkgs.python312.withPackages (ps: [
          ps.argon2-cffi
          ps.jsonschema
          ps.psycopg
          ps.psycopg-pool
          ps.starlette
          ps.uvicorn
          ps.websockets
          ps.httpx
          ps.pytest
          ps.pytest-asyncio
        ]);
    in
    {
      formatter = forAllSystems (system: (pkgsFor system).nixfmt);

      devShells = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = [
              # The server targets a pinned interpreter; the distribution
              # default drifts, so never use bare `python3`.
              (serverEnv pkgs)
              pkgs.ruff
              pkgs.postgresql
              pkgs.nixfmt
            ];
            shellHook = ''
              export PYTHONPATH="$PWD/neuman-server/src:$PWD/neuman-client/src:$PWD/neuman-admin/src:$PWD/contracts/gen/python:$PYTHONPATH"
            '';
          };
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          python = serverEnv pkgs;
        in
        {
          # Mechanical style: lint + format over every Python part (generated
          # code excluded via each part's pyproject.toml).
          #
          # `nix/` is included because the VM tests' probes are Python that
          # drives production adapters — `client_probe.py` and
          # `gateway_probe.py` are the only callers of some of it. They were
          # outside this check until an unused import in a new one made it
          # obvious they had never been linted at all.
          python-lint = pkgs.runCommand "python-lint" { } ''
            cd ${self}
            export RUFF_CACHE_DIR=$TMPDIR/ruff-cache
            ${pkgs.ruff}/bin/ruff check contracts neuman-server neuman-client neuman-admin tools nix
            ${pkgs.ruff}/bin/ruff format --check contracts neuman-server neuman-client neuman-admin tools nix
            touch $out
          '';

          # The same rule for Nix. Three files had drifted out of format before
          # this existed, because `python-lint` covered every
          # language *except* the one the appliance is assembled in — a gap that
          # is invisible precisely because nothing complains.
          nix-lint = pkgs.runCommand "nix-lint" { } ''
            cd ${self}
            ${pkgs.nixfmt}/bin/nixfmt --check $(find . -name '*.nix' -not -path './.git/*')
            touch $out
          '';

          # A syntax error in a VM test's Python fails the *driver* derivation
          # with a line number in generated code (`testScriptWithTypes`), which
          # is a long way from the file that caused it — and only after the
          # guest closure has built. Parsing them here costs a second.
          vm-testscripts = pkgs.runCommand "vm-testscripts" { } ''
            shopt -s failglob   # an empty glob must fail, not silently skip
            found=0
            for f in ${self}/nix/tests/*.nix; do
              ${(pyEnv pkgs)}/bin/python3 ${self}/nix/tests/check_testscript.py "$f"
              found=$((found + 1))
            done
            # A check that inspected nothing is not a check that passed. The
            # first version of this globbed a path that did not exist, so the
            # loop body never ran and `touch $out` reported success.
            test "$found" -ge 4 || { echo "only $found test scripts found"; exit 1; }
            touch $out
          '';

          # The contracts triad: schemas pass meta-validation, fixtures
          # behave, generated artifacts are fresh (zero-diff).
          contracts-test = pkgs.runCommand "contracts-test" { } ''
            cd ${self}/contracts
            export PYTHONDONTWRITEBYTECODE=1
            export HOME=$TMPDIR
            ${(pyEnv pkgs)}/bin/python3 -m codegen generate --check
            ${(pyEnv pkgs)}/bin/python3 -m codegen validate
            ${(pyEnv pkgs)}/bin/python3 -m pytest tests -q -p no:cacheprovider
            touch $out
          '';

          # The decision core's unit tests, including the layering contracts
          # (D7) in tests/test_layering.py. Every port has a fake, so this
          # needs no database and no model.
          server-test = pkgs.runCommand "server-test" { } ''
            cd ${self}/neuman-server
            export PYTHONDONTWRITEBYTECODE=1
            export HOME=$TMPDIR
            export PYTHONPATH="${self}/neuman-server/src:${self}/contracts/gen/python"
            ${python}/bin/python3 -m pytest tests -q -p no:cacheprovider
            touch $out
          '';

          # The client executor: start a process, drain both pipes, enforce the
          # deadline, bound and spill output, kill the group. No transport yet —
          # this is the half that runs a tool.
          client-test = pkgs.runCommand "client-test" { } ''
            cd ${self}/neuman-client
            export PYTHONDONTWRITEBYTECODE=1
            export HOME=$TMPDIR
            export PYTHONPATH="${self}/neuman-client/src"
            ${python}/bin/python3 -m pytest tests -q -p no:cacheprovider
            touch $out
          '';

          # The admin CLI. It needs the server on PYTHONPATH — not to import
          # it, but because `test_cli.py` checks every command against the
          # server's real router. A CLI is a second description of an API, and
          # two descriptions drift.
          admin-test = pkgs.runCommand "admin-test" { } ''
            cd ${self}/neuman-admin
            export PYTHONDONTWRITEBYTECODE=1
            export HOME=$TMPDIR
            export PYTHONPATH="${self}/neuman-admin/src:${self}/neuman-server/src:${self}/contracts/gen/python"
            ${python}/bin/python3 -m pytest tests -q -p no:cacheprovider
            touch $out
          '';

          # The first VM test. Cross-part invariants live here rather than in a
          # part's own suite, because they need a booted machine: real systemd
          # units, real sockets with real modes, real users.
          #
          # The appliance assembled: `services.neuman.enable = true` on a
          # booted machine, with Postgres migrated and the server listening.
          # The first test of the *module* rather
          # than of a seam. It needs a connected client before a turn can
          # dispatch; until one runs in the guest, the dispatch assertions are
          # skipped rather than silently passing.
          vm-appliance = import ./nix/tests/appliance.nix {
            inherit self pkgs;
          };

          # The gateway seam: the real client, a real socket, a real key file.
          # `test_gateway.py` covers the wire format over an in-process
          # transport; this covers the part that was broken — a secret
          # travelling from a file, through the unit's environment, into a
          # header on a request that actually left the process (N38).
          vm-gateway = import ./nix/tests/gateway.nix {
            inherit self pkgs;
          };

          # The image's configuration, booted. `nix build .#iso` proves the
          # image compiles; this proves an operator who boots it gets a
          # working appliance — every value in `nix/iso/demo.nix` is a
          # decision that can be wrong in a way evaluation cannot see.
          vm-iso = import ./nix/tests/iso.nix {
            inherit self pkgs;
          };

          # Restart and reboot: the case every other test skips. They all boot
          # into a fresh appliance, which is the state one is in exactly once.
          # This one has data first, then restarts — which is what catches
          # migrations against a populated database, state in a tmpfs, and a
          # socket left behind that makes the next bind fail.
          vm-restart = import ./nix/tests/restart.nix {
            inherit self pkgs;
          };

          # A model on this machine, end to end: fetched from a mirror of the
          # Hub, verified, added from the admin surface, and served by the real
          # router on the CPU through the server's own resolver. Also what only
          # a booted machine can check: which unit can write the weights, which
          # holds the devices, and what the router offers (D78, D79).
          vm-runtime = import ./nix/tests/runtime.nix {
            inherit self pkgs;
          };
        }
      );

      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        # `rec` because `neuman-tools` takes `neuman-admin` as an input: the
        # read-only wrapper execs the real CLI by absolute store path, because
        # the bin directory the tools live in is not on anyone's PATH and a
        # lookup would find nothing.
        rec {
          # The decision core, as the appliance installs it.
          #
          # The contracts package rides along in the same environment rather
          # than being vendored: generated artifacts are committed
          # (`contracts/gen`) and both parts must read the *same* schemas, so
          # copying them here would create a second installation of something
          # that is meant to have exactly one.
          neuman-server =
            let
              contractsPackage = pkgs.python312Packages.buildPythonPackage {
                pname = "neuman-contracts";
                version = "0.1.0";
                src = "${self}/contracts";
                format = "pyproject";
                nativeBuildInputs = [ pkgs.python312Packages.setuptools ];
                propagatedBuildInputs = [ pkgs.python312Packages.jsonschema ];
                doCheck = false;
              };
            in
            pkgs.python312Packages.buildPythonApplication {
              pname = "neuman-server";
              version = "0.1.0";
              src = "${self}/neuman-server";
              format = "pyproject";
              nativeBuildInputs = [ pkgs.python312Packages.setuptools ];
              propagatedBuildInputs = with pkgs.python312Packages; [
                contractsPackage
                argon2-cffi
                jsonschema
                psycopg
                psycopg-pool
                starlette
                uvicorn
                websockets
                httpx
              ];
              # The suite needs the tools/ directory and the test fakes, which
              # are not part of the installed package. `server-test` runs it
              # against the source tree instead.
              doCheck = false;
              meta.description = "Neuman's decision core";
            };

          # The tool set, as the appliance installs it: one directory per tool,
          # each holding its manifest. This is the server's installation of the
          # manifest contract; every client carries its own from its own store
          # path and never accepts one the server supplies at dispatch time (D3).
          #
          # Manifests only. The executable a `client` tool's `exec` names is
          # resolved by the client from `neuman-tools`, which is a separate
          # package for the reason below.
          neuman-registry = pkgs.runCommand "neuman-registry" { } ''
            mkdir -p $out
            cp -r ${self}/tools/. $out/
          '';

          # The browser the appliance drives is part of the client now:
          # `neuman-browser` installs from `neuman-client`, because it runs on
          # the user's machine and starts their Chromium. The chat window's
          # static files move with it, into `neuman-client/web/`: they are
          # served by the client and used by nothing else.

          # The tool executables. Separate from `neuman-registry` (manifests
          # only) because they are two different trust positions: a manifest is
          # read by the server, while the binary a name resolves to is the
          # *client's* own trusted configuration and never comes from a dispatch
          # (D3). `neuman-client` bundles both.
          neuman-tools = pkgs.callPackage ./tools {
            inherit (pkgs) git;
          };

          # The program on the acting human's machine: the executor and the
          # browser host. It carries the manifest set and the tool executables as
          # build inputs, so a dispatch cannot name a program outside them, and a
          # user runs one package rather than three flags.
          neuman-client = pkgs.callPackage ./neuman-client {
            inherit neuman-registry neuman-tools;
          };

          # The bootable image, as `nix build .#iso`. Not in `checks`: it is
          # a large build and its correctness is the module's, which
          # `vm-appliance` already gates.
          iso = self.nixosConfigurations.neuman-iso.config.system.build.isoImage;

          # The operator's CLI. A separate package from the server because it
          # is a separate trust position: the server is a daemon holding the
          # pool, and this is a short-lived process an operator runs. It shares
          # no code with the server — only the wire shapes — so it installs on
          # a machine that does not run one.
          neuman-admin = pkgs.python312Packages.buildPythonApplication {
            pname = "neuman-admin";
            version = "0.1.0";
            src = "${self}/neuman-admin";
            format = "pyproject";
            nativeBuildInputs = [ pkgs.python312Packages.setuptools ];
            propagatedBuildInputs = [ pkgs.python312Packages.httpx ];
            # The suite checks its commands against the server's router, which
            # is not a dependency of this package. `admin-test` runs it.
            doCheck = false;
            meta = {
              description = "Operator CLI for a Neuman appliance";
              # So `lib.getExe` does not have to guess the binary name.
              mainProgram = "neuman-admin";
            };
          };
        }
      );

      # A bootable demonstration image. The first artifact that answers "what
      # does an operator actually receive" — and building one forces every
      # question the module could leave open: what is on the image, what the
      # machine is called before anyone configures it, and whether it comes up
      # with no human to fix it.
      nixosConfigurations.neuman-iso = import ./nix/iso {
        inherit self nixpkgs;
        system = "x86_64-linux";
      };

      # The module, with its packages defaulted to this flake's. `nix/module`
      # cannot know which flake it came from, so the defaults are set here, and
      # a deployment importing this names none of them.
      nixosModules.neuman =
        { pkgs, lib, ... }:
        let
          packages = self.packages.${pkgs.stdenv.hostPlatform.system};
        in
        {
          imports = [ ./nix/module ];
          services.neuman = {
            package = lib.mkDefault packages.neuman-server;
            adminPackage = lib.mkDefault packages.neuman-admin;
            registry = lib.mkDefault packages.neuman-registry;
          };
        };
      nixosModules.default = self.nixosModules.neuman;

      # The user-side half. A home-manager module rather than a NixOS one: it
      # installs a per-user browser, a desktop entry, and a user service, none
      # of which belong to the system profile.
      homeModules.neuman-client = import ./nix/module/client.nix;

      # Filled in as parts land:
      #   packages.<system>.neuman-server       — the decision core
      #   nixosModules.neuman                  — services.neuman.*
      #   checks.<system>.vm-*                 — the invariant / adversarial tests
    };
}
