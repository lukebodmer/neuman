# `services.neuman.*` — the appliance.
#
# One machine: Postgres and the decision core. The
# client — the executor, the browser and the UI — runs on the machine of the
# human it acts for, configured by `nix/module/client.nix`. This is the first
# place the server's parts are composed rather than tested in isolation, and
# the composition is most of what the appliance *is*: the units below encode
# the trust tiers `docs/security-model.md` describes, and getting one of them
# wrong is how a boundary quietly stops existing.
#
# What this module deliberately does **not** do: configure Neuman. Accounts,
# roles, providers, models and the prompt live in Postgres and are made in the
# administration page or with `neuman-admin` (D16, D68); a fresh appliance
# starts empty. The module owns what a restart changes — packages, paths,
# ports, TLS, the units and their sandboxes — and one host job: the OS `neuman`
# group, which decides who may open the admin socket and therefore who can
# create the first administrator.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.neuman;
in
{
  # The model runtimes and the weights fetcher, kept in their own file: they
  # are separate units with their own sandboxes (D78, D79).
  imports = [
    ./runtime.nix
  ];

  options.services.neuman = {
    enable = lib.mkEnableOption "the Neuman appliance";

    # No defaults here: this file does not know which flake it came from. The
    # flake's `nixosModules.neuman` sets all three to its own packages, so a
    # deployment importing it names none of them.
    package = lib.mkOption {
      type = lib.types.package;
      description = "The decision core.";
    };

    adminPackage = lib.mkOption {
      type = lib.types.package;
      description = "The operator CLI (`neuman-admin`).";
    };

    maxTokens = lib.mkOption {
      type = lib.types.ints.positive;
      default = 4096;
      example = 8192;
      description = ''
        The fallback for how much a model may generate in one step of a turn,
        used only by a model that declares no `maxTokens` of its own.

        **Prefer the per-model value.** How long a reply may run is a property
        of the model and its hardware, not of the appliance: a 65k local model
        and a 1M hosted one differ by a factor of sixteen in the window a reply
        is carved out of, so one number cannot be right for both. Every model in
        Neuman's catalog states its own, and this is what an unlisted one gets.
        It is a *fallback*, not a cap — a model stating 131072 gets 131072
        whatever this says.

        Not a budget either: spending is bounded by `policy.tokens_daily` and
        the window by the model's own `contextTokens`. This is the room one
        reply gets, and too little is how a model ends up cut off mid-thought —
        a reasoning model routinely spends thousands of tokens before its first
        visible word, and at the 4096 default that was five truncations in six
        hours on a local 27B (gotchas N90). A truncated reply is recovered from
        rather than fatal, so this decides how often that recovery is needed,
        not whether work is lost.

        Whichever value applies shares the context window with the prompt: the
        server reserves the reply's ceiling plus a margin and trims history
        against the remainder, so a larger reply means less conversation. On a
        32k window, 8192 leaves roughly 21k for history.

        Read by the server as `NEUMAN_MAX_TOKENS`.
      '';
    };

    homeDataDirs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "Documents"
        "Desktop"
        "Downloads"
        "workspace"
      ];
      example = [
        "projects"
        "Documents"
      ];
      description = ''
        What a `$HOME` entry in `files_read` or `files_write` expands to.

        A `$HOME` entry names these subdirectories rather than the home itself, so
        the declared surfaces are the places work actually happens — `~/.ssh`,
        `~/.bashrc` and `~/.config/systemd` are not among them. `$HOME` is the
        spelling to prefer: it names the home of whichever machine runs the tool,
        which the client reports, where an absolute path names one machine's
        directory. The client runs as the user and reaches what the user reaches,
        so this list is what policy declares and what the working-directory check
        compares against, not a boundary. `shell` and `python` are not
        constrained by it at all.

        So this is the list of places work happens. Granting a home plus naming
        `projects` here is one policy entry that covers every project under it,
        rather than a whitelist that grows.

        Read by the server as `NEUMAN_HOME_DATA_DIRS`.
      '';
    };

    adminSocketAccess = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "you" ];
      description = ''
        OS accounts that may *reach* the admin socket, added to the `neuman`
        group. Usually just the person who installs the appliance.

        This is reachability only. **Authority is membership in `neuman-admin`
        in Neuman's own account store**, which `neuman-admin bootstrap` creates
        over this socket. An account listed here without that row can open the
        socket and is refused by `require_admin` on every request.

        Note that group membership is fixed at login, so a rebuild that grants
        this needs a fresh session before `neuman-admin` can connect (N41).
      '';
    };

    registry = lib.mkOption {
      type = lib.types.package;
      description = ''
        The tool manifests. The server and every client read the *same* store
        path, which is what makes D3's two installations two readings of one
        contract rather than two contracts.
      '';
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8443;
      description = "Where the session surface listens.";
    };

    bind = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = ''
        The listen address. Defaults to loopback: the session surface is the
        untrusted network surface (D1), and exposing it should be a decision
        an operator makes rather than a default they inherit.
      '';
    };

    tls = {
      certificate = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = ''
          TLS certificate. Without it the server still starts — a reverse
          proxy may terminate TLS — but it warns, because session cookies are
          `Secure`, so a client on another machine will not present one over
          plain HTTP.
        '';
      };
      key = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = "TLS private key. Must be set with the certificate.";
      };
    };

    retention = {
      sessionDays = lib.mkOption {
        type = lib.types.int;
        default = 0;
        description = "0 keeps sessions forever, which is the default.";
      };
      auditDays = lib.mkOption {
        type = lib.types.int;
        default = 0;
        description = ''
          0 keeps the audit chain forever. Purging is from the front only, and
          the JSONL mirror keeps the full history regardless — the purge bounds
          the database, not the record.
        '';
      };
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = (cfg.tls.certificate == null) == (cfg.tls.key == null);
        message = "services.neuman.tls: set certificate and key together, or neither.";
      }
    ];

    # -- identity ------------------------------------------------------------

    # Group *existence* is declared here, in one place.
    #
    users.groups.neuman = { };

    users.users.neuman = {
      isSystemUser = true;
      group = "neuman";
      description = "Neuman decision core";
    };

    # -- storage (D18) -------------------------------------------------------
    #
    # One database, reached over the local socket with peer authentication, so
    # there is no password anywhere in the configuration and exactly one role
    # can write the core's state.
    services.postgresql = {
      enable = true;
      ensureDatabases = [ "neuman" ];
      ensureUsers = [
        {
          name = "neuman";
          ensureDBOwnership = true;
        }
      ];
    };

    systemd.tmpfiles.rules = [
      # The server's own directories are not here: `StateDirectory`,
      # `LogsDirectory` and `RuntimeDirectory` on the unit make them every time
      # it starts. Rules here run at boot and when they change, so a directory
      # deleted in between — starting over — was a unit that could not set up
      # its sandbox until the next reboot (N167).
      #
      # Backups land here by default. The server runs under
      # ProtectSystem=strict, so it can only write inside its state directory —
      # a backup to an arbitrary path fails with EROFS, which reads as a
      # permissions problem and is actually a namespace one (N29).
      #
      # 0700: a backup contains the whole database, including the audit
      # chain. It is the most sensitive artifact the appliance produces and
      # the one most likely to be copied somewhere careless.
      "d /var/lib/neuman/backups 0700 neuman neuman -"
      "d /run/neuman 0755 root root -"
      # The GPU sample lives in its own directory for the same reason the admin
      # socket does: `/run/neuman` is root-owned, and a directory `neuman` can
      # write to is a directory in which it could replace the socket.
      # `ReadWritePaths` cannot help — that lifts systemd's protection, not the
      # mode bits — which is why the first version of the sampler failed with
      # `Permission denied` on `/run/neuman/gpu.json.new` (N73).
      #
      # 0755 rather than 0750: the file is a status report an operator may want to
      # read with `cat`, and it contains how full the GPU is and which process names
      # hold memory — not a secret. The *directory* being writable only by `neuman`
      # is the part that matters.
      "d /run/neuman/gpu 0755 neuman neuman -"
    ];

    # -- the decision core ---------------------------------------------------

    systemd.services.neuman-server = {
      description = "Neuman decision core";
      wantedBy = [ "multi-user.target" ];
      # After the setup unit, not only the server: `ensureDatabases` and
      # `ensureUsers` run in `postgresql-setup`, so ordering on `postgresql`
      # alone started this against a database that might not exist yet.
      after = [
        "network.target"
        "postgresql-setup.service"
      ];
      requires = [ "postgresql-setup.service" ];

      environment = {
        NEUMAN_REGISTRY_DIR = "${cfg.registry}";
        NEUMAN_STATE_DIR = "/var/lib/neuman/state";
        NEUMAN_AUDIT_MIRROR = "/var/log/neuman/audit.jsonl";
        NEUMAN_ADMIN_SOCKET = "/run/neuman/admin/admin.sock";
        NEUMAN_HOME_DATA_DIRS = lib.concatStringsSep ":" cfg.homeDataDirs;
        NEUMAN_MAX_TOKENS = toString cfg.maxTokens;
        NEUMAN_MODELS_DIR = cfg.modelsDir;
        NEUMAN_BIND = cfg.bind;
        NEUMAN_PORT = toString cfg.port;
        NEUMAN_SESSION_RETENTION_DAYS = toString cfg.retention.sessionDays;
        NEUMAN_AUDIT_RETENTION_DAYS = toString cfg.retention.auditDays;
        # The server's own store path, stamped into every turn's log. A content
        # hash, so two turns carrying the same value provably ran the same code
        # — which is what makes "did this behaviour change, or was the earlier
        # report wrong?" answerable rather than inferable. Nothing else on this
        # appliance identifies a build at runtime: the store path is the one
        # identifier that already exists and cannot drift from what is running.
        NEUMAN_BUILD_ID = "${cfg.package}";
      }
      // lib.optionalAttrs (cfg.tls.certificate != null) {
        NEUMAN_TLS_CERT = toString cfg.tls.certificate;
        NEUMAN_TLS_KEY = toString cfg.tls.key;
      };

      serviceConfig = {
        ExecStart = "${cfg.package}/bin/neuman-server";
        # `pg_dump` and `pg_restore` are invoked by the backup and restore
        # routes. They are not a Python dependency, so nothing else would put
        # them on the unit's PATH.
        Environment = [ "PATH=${lib.makeBinPath [ config.services.postgresql.package ]}" ];
        User = "neuman";
        Group = "neuman";
        Restart = "on-failure";
        # A configuration error is not worth retrying: it will fail the same
        # way every time and the restart loop hides the message.
        RestartPreventExitStatus = "78";

        # Never root. What it has is the database socket, its own state, and
        # the admin socket — nothing more.
        NoNewPrivileges = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictRealtime = true;
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
        ];
        MemoryDenyWriteExecute = true;

        # The server's writable places, made by systemd on every start and
        # owned by `neuman`, rather than by tmpfiles at boot: a directory that
        # is missing is created, not a sandbox that fails with 226/NAMESPACE
        # (N167). Writable inside `ProtectSystem=strict` because systemd made
        # them; nothing else is.
        #
        # `/var/lib/neuman` holds `state/` — which the server makes, with the
        # overlays and the `0700` key directory under it — `backups/` and the
        # weights. `/run/neuman/admin` is the socket's directory: `/run/neuman`
        # itself stays root-owned, so a directory the server could replace
        # wholesale is never the boundary, and 0750 restricts traversal to the
        # `neuman` group (the socket's own 0660 is the check that matters).
        StateDirectory = "neuman";
        StateDirectoryMode = "0750";
        LogsDirectory = "neuman";
        LogsDirectoryMode = "0750";
        RuntimeDirectory = "neuman/admin";
        RuntimeDirectoryMode = "0750";
        # The runtime host's and the fetcher's sockets, which the server only
        # connects into. Optional (`-`), so a `/run` being rebuilt is not a
        # unit that refuses to start.
        ReadWritePaths = [ "-/run/neuman" ];
        # The weights are under the state directory and are not the server's
        # to write: the fetcher puts them there and verifies them (D79), and a
        # server that could replace one could choose what the GPU runs.
        ReadOnlyPaths = [ "-${cfg.modelsDir}" ];
      };
    };

    environment.systemPackages = [
      cfg.package
      # An appliance an operator cannot administer is not one they can fix, so
      # the CLI is installed rather than left to be fetched.
      cfg.adminPackage
    ];

    # Reaching the admin socket requires being in the `neuman` group. The
    # socket's mode (0660, set by the server before it listens) is what makes
    # this meaningful: without it the group would be decoration, since anyone
    # could connect and the only check would be the one inside the core.
    #
    # Reachability only. Authority is the database's `neuman-admin` group, so
    # being in this OS group lets you open the socket and nothing more.
    users.groups.neuman.members = cfg.adminSocketAccess;

    warnings = lib.optional (cfg.adminSocketAccess == [ ]) ''
      services.neuman.adminSocketAccess is empty, so no OS account can reach
      /run/neuman/admin/admin.sock. Nobody can run `neuman-admin bootstrap` on
      this machine until one is named. Set it to the account that installs the
      appliance:

        services.neuman.adminSocketAccess = [ "you" ];
    '';
  };
}
