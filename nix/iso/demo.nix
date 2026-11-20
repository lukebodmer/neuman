# What the demonstration image *is*, separate from how it is packaged.
#
# Split out so `nix/tests/iso.nix` boots this exact configuration rather than a
# hand-copied version of it. A test that restates the thing it is testing goes
# on passing while the two drift — which is the shape of N35, and not a mistake
# worth making twice in one repository.
#
# Image packaging (squashfs, the bootloader, the ISO name) stays in
# `nix/iso/default.nix`, because none of it is testable without booting real
# removable media.
{ self, system }:

{
  config,
  lib,
  pkgs,
  ...
}:

let
  packages = self.packages.${system};

  # A demonstration account. The OS user is the socket peer; the Neuman account
  # is created in the database by the bootstrap unit below, with the same name
  # because one person has one name on this machine.
  demoPassword = "neuman-demo-password";

  # A policy narrow enough to be honest. The demo account can read and write
  # inside one directory and nothing else — the claim the appliance exists to
  # make, stated in the smallest form that still does something. Set through
  # the admin socket by the unit below, the way an operator configures any
  # appliance: the module carries no policy (D68).
  baseline = pkgs.writeText "demo-baseline.json" (
    builtins.toJSON {
      schema_version = 2;
      defaults = { };
      roles.demo = {
        tools = [
          "fs_read"
          "fs_image"
          "fs_list"
          "fs_search"
          "fs_write"
          "shell"
          # A command past its two-minute wait keeps running as a job the
          # model can follow and stop, rather than being killed (D98).
          "job_output"
          "job_kill"
        ];
        files_read = [ "/home/demo/workspace" ];
        files_write = [ "/home/demo/workspace" ];
        # Every mutating dispatch asks. On an image whose purpose is to be
        # looked at, the interesting thing to watch is the approval.
        auto_accept = "never";
      };
      users = [
        {
          name = "demo";
          roles = [ "demo" ];
        }
      ];
    }
  );
in
{
  networking.hostName = "neuman";

  users.users.demo = {
    isNormalUser = true;
    description = "Demonstration account";
    password = demoPassword;
    # `wheel` for the ordinary reason an interactive demo account wants sudo;
    # it confers nothing on Neuman. Admin authority is the database group the
    # bootstrap adds.
    extraGroups = [ "wheel" ];
  };

  services.neuman = {
    enable = true;
    adminSocketAccess = [ "demo" ];

    # The CPU builds of the runtimes. A demo boots on hardware nobody chose, and
    # the CUDA ones are unfree and gigabytes; it serves hosted models.
    gpu = "none";

    # All interfaces, which the module deliberately does *not* default to: D1
    # makes exposing the session surface an operator's decision rather than an
    # inherited default. This image is that decision — its entire purpose is to
    # be opened in a browser, and on loopback it cannot be. A QEMU port forward
    # or a VM on a host-only network both deliver to the guest's *interface*,
    # so a server on 127.0.0.1 accepts nothing and the browser hangs forever
    # with no error anywhere (N42).
    #
    # Safe only because of what this image is: no persistent state, a public
    # password, and a lifetime measured in minutes. A real deployment keeps the
    # loopback default and puts TLS in front.
    bind = "0.0.0.0";
  };

  # The other half of reachability, and the same symptom when missing: NixOS
  # blocks by default, so a bound port behind a closed firewall also hangs with
  # no error. Both had to be wrong for the browser to hang, and fixing either
  # alone would have looked like no progress.
  networking.firewall.allowedTCPPorts = [
    config.services.neuman.port
    8642
  ];

  # The workspace the policy names. Without it the first dispatch fails on a
  # missing directory, which reads as a broken appliance rather than an empty
  # one.
  systemd.tmpfiles.rules = [
    "d /home/demo/workspace 0755 demo users -"
  ];

  # The chat window is the client's, and on this image the client runs here too:
  # the appliance and the machine it is looked at from are the same box. The
  # client logs in with the public password, holds the session, and serves the UI
  # locally, so the browser talks to 8642 and never to the API.
  #
  # `--ui-host 0.0.0.0` because a browser reaching this image arrives through a
  # port forward or a host-only network to the *interface*, and a client on
  # loopback would accept nothing — the same N42 shape the server's bind had.
  # The token still gates the local API; the console says where to read it.
  environment.etc."neuman/demo-password" = {
    text = demoPassword;
    mode = "0644";
  };

  # What an operator does by hand on a real appliance, done at boot. The
  # account is a row, and the first row has to exist before the client can log
  # in: the bootstrap route is the one admin request a local peer may make
  # before an administrator exists. Then the policy, through the same socket.
  systemd.services.neuman-bootstrap = {
    wantedBy = [ "multi-user.target" ];
    after = [ "neuman-server.service" ];
    requires = [ "neuman-server.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = pkgs.writeShellScript "neuman-demo-bootstrap" ''
        until test -S /run/neuman/admin/admin.sock; do sleep 0.2; done
        admin() { ${pkgs.util-linux}/bin/runuser -u demo -- ${packages.neuman-admin}/bin/neuman-admin "$@"; }
        admin bootstrap --name demo --password-file /etc/neuman/demo-password --force
        admin baseline set ${baseline}
      '';
    };
  };

  systemd.services.neuman-client = {
    wantedBy = [ "multi-user.target" ];
    after = [
      "neuman-server.service"
      "neuman-bootstrap.service"
    ];
    requires = [ "neuman-bootstrap.service" ];
    serviceConfig = {
      User = "demo";
      ExecStartPre = "${packages.neuman-client}/bin/neuman-client login --server http://127.0.0.1:${toString config.services.neuman.port} --user demo --password-file /etc/neuman/demo-password";
      ExecStart = "${packages.neuman-client}/bin/neuman-client run --server http://127.0.0.1:${toString config.services.neuman.port} --ui-host 0.0.0.0";
      Restart = "on-failure";
      RestartSec = 2;
    };
  };

  environment.systemPackages = [
    pkgs.curl
    packages.neuman-client
  ];

  # Said on the console, because an operator who boots this needs to know it
  # is not a deployment before they put anything in it.
  services.getty.helpLine = lib.mkForce ''

    Neuman — demonstration image

      The appliance is running at http://localhost:${toString config.services.neuman.port}
      The chat UI is served by the client at http://localhost:8642 — its URL,
      token included, is:
        cat /home/demo/.local/share/neuman/client/ui-url
      (substitute this image's address for 127.0.0.1). Sign in as `demo` with
      the password `neuman-demo-password`.
      Administer it with `neuman-admin status`.

    NOT A PRODUCTION INSTALL. The password is public, TLS is absent, and the
    whole system lives in RAM. See nix/README.md before deploying.
  '';

}
