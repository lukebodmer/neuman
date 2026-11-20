# A bootable appliance image.
#
# The point is not convenience. An ISO is the first artifact that answers
# "what does an operator actually receive", and building one forces every
# question the module could leave open: what is on the image, what the machine
# is called before anyone configures it, and whether the thing comes up at all
# with no human to fix it.
#
# **This is a demonstration image, not a production install.** It runs from the
# live medium with a well-known password and no TLS, so an operator can boot
# it, log in, and watch the whole thing work before deciding to deploy. The
# console says so at every login; `nix/README.md` says what must change.
#
# What it deliberately does *not* do is partition a disk. An installer that
# writes to block devices is a different artifact with a different blast
# radius, and "boot this and the appliance is running" does not need one.
#
# Only packaging lives here. What the image *is* — the account, its policy,
# the workspace — is `demo.nix`, so `checks.vm-iso` can boot that exact
# configuration instead of a hand-copied version of it.
{
  self,
  nixpkgs,
  system,
}:

nixpkgs.lib.nixosSystem {
  inherit system;
  modules = [
    "${nixpkgs}/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix"
    self.nixosModules.neuman
    (import ./demo.nix { inherit self system; })
    (
      { lib, pkgs, ... }:
      {
        # A live image has no persistent state, so nothing survives a reboot —
        # including the audit chain. Saying so once at build time is cheaper
        # than an operator discovering it.
        warnings = [
          "The Neuman ISO is a demonstration image: state is in RAM and does not survive a reboot."
        ];

        # The installer ISO tracks a recent kernel because there is no reason for
        # a demonstration image to carry an old one. `mkDefault`, so an operator
        # who wants a pinned kernel still can.
        boot.kernelPackages = lib.mkDefault pkgs.linuxPackages_latest;

        # `image.baseName`, not `isoImage.isoName` and not `image.fileName`.
        #
        # Three options, one of which works. `isoImage.isoName` is renamed and
        # evaluates with a deprecation warning while doing nothing.
        # `image.fileName` evaluates to exactly the string you set — and the
        # build still emits `nixos-minimal-...`, because the file is named
        # from `baseName` and `fileName` is derived from it, not the reverse.
        #
        # Both wrong spellings *evaluate clean*. `nix eval` on either returns
        # the name you wanted, which is why this took three builds to notice:
        # the only way to see it was to list the files inside the built
        # artifact (N37).
        image.baseName = lib.mkForce "neuman-demo";

        isoImage = {
          volumeID = lib.mkForce "NEUMAN";
          makeEfiBootable = true;
          makeUsbBootable = true;
        };

        system.stateVersion = lib.versions.majorMinor lib.version;
      }
    )
  ];
}
