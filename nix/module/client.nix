# The user-side half of a Neuman appliance: the client.
#
# Importing this in a home-manager configuration installs the client — the
# executor, which serves the UI and runs the tools, and the browser host that
# backs `browse` — and runs both as user services. It complements
# `services.neuman` on the server: that half decides, this half executes and
# browses.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.neuman-client;

  # What the tools dial for `browse`, and where this file's browser bridge
  # listens. One value, passed to both services, so they cannot drift.
  socket = "%t/neuman-browse.sock";

  stateDir = "${config.home.homeDirectory}/.local/share/neuman/client";

  browserArguments = [
    "--port"
    (toString cfg.browserPort)
    "--socket"
    cfg.socket
    "--profile"
    cfg.profile
    "--user-data-dir"
    cfg.userDataDir
  ];

  clientExec = "${cfg.package}/bin/neuman-client run --server ${cfg.server} --ui-port ${toString cfg.uiPort} --browse-socket ${cfg.socket}";
  browserExec = "${cfg.package}/bin/neuman-browser ${lib.escapeShellArgs browserArguments}";
in
{
  options.programs.neuman-client = {
    enable = lib.mkEnableOption "the Neuman client: the executor, the UI and the browser";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../../neuman-client {
        neuman-registry = pkgs.runCommand "neuman-registry" { } ''
          mkdir -p $out
          cp -r ${../../tools}/. $out/
        '';
        neuman-tools = pkgs.callPackage ../../tools { inherit (pkgs) git; };
      };
      defaultText = lib.literalExpression "pkgs.neuman-client";
      description = "The client package to install.";
    };

    server = lib.mkOption {
      type = lib.types.str;
      example = "https://appliance.example:8443";
      description = ''
        The core to connect to. The client logs in there once and holds the
        session; the browser only ever sees the client.
      '';
    };

    uiPort = lib.mkOption {
      type = lib.types.port;
      default = 8642;
      description = "The loopback port the client serves the chat UI on.";
    };

    browserPort = lib.mkOption {
      type = lib.types.port;
      default = 9222;
      description = "The loopback port Chromium's DevTools endpoint listens on.";
    };

    socket = lib.mkOption {
      type = lib.types.str;
      default = socket;
      description = ''
        Where the browser bridge listens, and what the `browse` tool dials.
        Both services are handed this one value, so it only needs changing if a
        deployment moves the bridge somewhere else.

        `%t` is the session's runtime directory — `0700`, cleared at logout —
        because the socket's own mode is the access control.
      '';
    };

    profile = lib.mkOption {
      type = lib.types.enum [
        "own"
        "copy"
      ];
      default = "own";
      description = ''
        Which cookies the browser holds.

        `own` (the default) is a profile belonging to Neuman: a page that
        injects the model acts with none of the user's logins. `copy` rsyncs
        their real profile in at startup, so Neuman can reach pages they are
        signed into — and a page that injects the model then acts with their
        identity. The dangerous one is opt-in for that reason.
      '';
    };

    userDataDir = lib.mkOption {
      type = lib.types.str;
      default = "~/.local/share/neuman/browser";
      description = "The Chromium profile directory, with `~` expanded by the client.";
    };

    autostart = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Start the browser at login rather than waiting for the launcher. An
        appliance that is always browsed is easier to use when its browser is
        already up.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];

    xdg.desktopEntries.neuman = {
      name = "Neuman";
      comment = "Neuman appliance";
      exec = "${pkgs.systemd}/bin/systemctl --user start neuman-browser.service";
      icon = "web-browser";
      terminal = false;
      categories = [
        "Network"
        "WebBrowser"
      ];
    };

    systemd.user.services.neuman-client = {
      Unit = {
        Description = "Neuman client";
        After = [ "graphical-session.target" ];
        PartOf = [ "graphical-session.target" ];
      };
      Service = {
        ExecStart = clientExec;
        Restart = "on-failure";
        RestartSec = 2;
      };
      Install.WantedBy = [ "default.target" ];
    };

    systemd.user.services.neuman-browser = {
      Unit = {
        Description = "Neuman browser";
        After = [
          "graphical-session.target"
          "neuman-client.service"
        ];
        PartOf = [ "graphical-session.target" ];
      };
      Service = {
        # The daemon writes the UI's URL — token included — when it starts, and
        # the browser reads it. Waiting here rather than racing it is what keeps
        # the browser from opening the core's address instead of the client's.
        ExecStartPre = "${pkgs.bash}/bin/bash -c ${
          lib.escapeShellArg (
            "for _ in ${pkgs.coreutils}/bin/seq 1 150; do [ -s ${stateDir}/ui-url ] && exit 0; "
            + "${pkgs.coreutils}/bin/sleep 0.2; done; exit 1"
          )
        }";
        ExecStart = browserExec;
        Restart = "on-failure";
        RestartSec = 2;
      };
      Install.WantedBy = lib.optional cfg.autostart "graphical-session.target";
    };
  };
}
