# The units beside the server that touch model weights (D78, D79).
#
# `neuman-runtime` holds the GPU and nothing else: it runs `llama-server` in
# router mode for the local models, from what the server's document names and
# the shipped presets describe. `neuman-fetch` holds the network and the
# weights directory and nothing else. The server has neither.
#
# Which models run is not configured here. It is the database's (D68): a model
# is added from its tile in the administration page, and the runtime host reads
# what was chosen without a rebuild. What is here is what only the machine can
# say — the accelerator, where the weights live, and which loopback port the
# router uses.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.neuman;

  # The runtime, built for whatever `services.neuman.gpu` says. The plain
  # nixpkgs package is a CPU build, and a default that disagreed with what the
  # appliance was for is the bug this replaces (N98).
  llama =
    if cfg.gpu == "cuda" then pkgs.llama-cpp.override { cudaSupport = true; } else pkgs.llama-cpp;

  # Whether this `pkgs` will let a CUDA package through at all.
  #
  # CUDA is unfree, and `nixpkgs.config` belongs to whoever instantiated `pkgs` —
  # a module cannot set it retroactively, and on a flake or a `pkgs.nixos` call it
  # is fixed before this module is read. So the licence is *checked* here and
  # reported as an assertion, rather than quietly overridden: a module that forced
  # `allowUnfree` would be deciding a licence question on the operator's behalf,
  # and for every other unfree package too.
  #
  # Checked with `tryEval` because the refusal is a thrown evaluation error, not a
  # boolean — reading the derivation is the only way to ask.
  cudaAllowed = (builtins.tryEval llama.drvPath).success;

  # Where the server tells the host what to run: in the server's state
  # directory, which the host can read and not write.
  runtimeConfig = "/var/lib/neuman/state/runtime.json";

  # One sampler, run by its timer and by the runtime host at the moments GPU
  # memory changes, so the two cannot disagree about what a sample is.
  sampleCommand = lib.escapeShellArgs [
    (lib.getExe pkgs.python3)
    ./gpu-sample.py
    "/run/neuman/gpu/state.json"
  ];
  cuda = cfg.gpu == "cuda";
in
{
  options.services.neuman.modelsDir = lib.mkOption {
    type = lib.types.str;
    default = "/var/lib/neuman/models";
    description = ''
      Where model weights live, owned by `neuman`, mode 0770. `neuman-fetch` is
      the only unit that writes here; the runtime host and the server read.

      Beside the appliance's other state rather than in a user's home, because
      the weights belong to the appliance: a second account uses the same model,
      and deleting a user should not take the models with them. It is also
      traversable by the model runtime, which a `0700` home is not — that was the
      entire `Permission denied` failure this directory exists to prevent (N52).

      Weights arrive from a model's tile in the administration page, which asks
      `neuman-fetch` to download the preset's pinned files (D79). A file put here
      by hand under a preset's name is adopted once the fetcher has hashed it.
    '';
  };

  options.services.neuman.gpuTools = lib.mkOption {
    type = lib.types.nullOr lib.types.package;
    default =
      if config.hardware.nvidia.package or null != null then config.hardware.nvidia.package.bin else null;
    defaultText = lib.literalExpression "config.hardware.nvidia.package.bin, or null";
    description = ''
      The package providing `nvidia-smi`, for the GPU sampler.

      Defaults to the *configured* driver — `hardware.nvidia.package` — rather than a
      fresh evaluation of `pkgs.linuxPackages.nvidia_x11`. Those are the same package
      by name and can be different versions, and a userspace `nvidia-smi` that does
      not match the running kernel module fails with
      `Driver/library version mismatch`, which the sampler could only report as "no
      GPU" (N79).

      Null on a machine with no NVIDIA driver, which the sampler reports as an
      absent GPU rather than an error — that is the ordinary state for an appliance
      running hosted models only.

      Set it explicitly for AMD or another vendor, once something other than
      `nvidia-smi` is supported.
    '';
  };

  options.services.neuman.gpu = lib.mkOption {
    type = lib.types.enum [
      "cuda"
      "none"
    ];
    default = "cuda";
    description = ''
      Which accelerator the model runtime is built for.

      `cuda` builds `llama-cpp` against CUDA. `none` takes the plain nixpkgs
      package, which runs on the CPU.

      **Why this defaults to `cuda`.** The runtime used to default to the plain
      nixpkgs package, which is a *CPU* build, on the reasoning that a GPU build is
      a multi-gigabyte compile and the module cannot see the hardware. The result
      was an appliance that asked for a GPU, was handed a CPU backend, and said so
      only in a log line nobody reads, with the weights loaded into system RAM and
      every request sixty times slower — which presents as a broken feature rather
      than as a packaging default (N98).

      That reasoning is also out of date. `cache.nixos-cuda.org` serves these builds,
      so this is a few hundred megabytes of download rather than a CUDA toolchain
      built from source. The flake declares that substituter,
      so a machine building Neuman gets it without having to discover it.

      And an appliance whose point is a local model on a local GPU should not quietly
      run on the CPU. Everything else here already assumes the GPU exists: the
      sampler, the VRAM panel, the runtime unit that relaxes `PrivateDevices` to
      reach the device. A CPU default contradicted all of it.

      Set `none` for an appliance with no NVIDIA GPU, or one serving only hosted
      models — it is the smaller closure and nothing here needs a GPU to start.
    '';
  };

  options.services.neuman.runtime = {
    chatPort = lib.mkOption {
      type = lib.types.port;
      default = 8081;
      description = ''
        The loopback port of the chat router, which serves every local chat
        model by name. The server reaches it here; nothing else needs to.
      '';
    };
    maxLoaded = lib.mkOption {
      type = lib.types.ints.positive;
      default = 1;
      description = ''
        How many chat models may be resident at once. One on a consumer card:
        selecting another model unloads the first, which the router does for
        itself. Raise it on a GPU that holds several.
      '';
    };
  };

  options.services.neuman.fetch.baseUrl = lib.mkOption {
    type = lib.types.str;
    default = "https://huggingface.co";
    description = ''
      Where `neuman-fetch` downloads weights from, as
      `<baseUrl>/<repo>/resolve/<commit>/<file>`: the Hugging Face Hub. A mirror
      serving the same paths works, and the VM test uses one. The bytes are
      checked against the preset's pinned hash either way.
    '';
  };

  config = lib.mkIf cfg.enable {
    # 0770: the fetcher writes here and the runtime reads.
    systemd.tmpfiles.rules = [ "d ${cfg.modelsDir} 0770 neuman neuman -" ];

    assertions = [
      {
        # Named here rather than left to nixpkgs' own refusal, which arrives as a
        # wall of text about `cuda_cccl` — a transitive dependency nobody asked
        # for — and never mentions Neuman, the option that pulled it in, or the one
        # line that resolves it.
        assertion = cfg.gpu != "cuda" || cudaAllowed;
        message = ''
          services.neuman.gpu is "cuda", but this nixpkgs refuses unfree packages
          and CUDA is unfree. Allow it:

              nixpkgs.config.allowUnfree = true;

          Note that `nixpkgs.config` belongs to whoever instantiated `pkgs`, so on a
          flake it goes where nixpkgs is imported, not in this module's scope — which
          is also why this is an assertion rather than something the module could fix
          on its own.

          Or set `services.neuman.gpu = "none"` to build the model runtime for the
          CPU. Be aware what that costs: a local model on the CPU is an order of
          magnitude slower or worse, which presents as a broken feature rather
          than as a slow backend (N98).
        '';
      }
    ];

    systemd.services = {
      # GPU telemetry, in a unit that does nothing else. The whole interface to
      # the server is one JSON file, so the worst a compromise here yields is a
      # lie about how full the GPU is.
      # Only with an NVIDIA GPU: `nvidia-smi` is the driver's, and a machine
      # running the CPU builds has neither, so the sampler would report an
      # absence every five seconds.
      neuman-gpu-sample = lib.mkIf (cfg.gpu == "cuda") {
        description = "Sample GPU state for Neuman";
        serviceConfig = {
          Type = "oneshot";
          ExecStart = sampleCommand;
          User = "neuman";
          Group = "neuman";

          # Read-only device access, and nothing else this unit could use.
          PrivateDevices = false;
          PrivateNetwork = true;
          NoNewPrivileges = true;
          ProtectSystem = "strict";
          ProtectHome = true;
          # Only its own directory. `/run/neuman` would let this unit write
          # beside the admin socket, and a GPU sampler has no business
          # there — `ReadWritePaths` is about intent as much as capability.
          ReadWritePaths = [ "/run/neuman/gpu" ];
          # The *configured* driver, not a fresh nixpkgs evaluation of the same
          # name. Pinning `pkgs.linuxPackages.nvidia_x11.bin` gave 595.99.02 while
          # the running kernel module was 595.45.04, and `nvidia-smi` answered
          # `Failed to initialize NVML: Driver/library version mismatch` — so the
          # sampler reported `available: false` with a 3090 in the machine (N79).
          #
          # `hardware.nvidia.package` is where the kernel module came from, so the
          # userspace tool is guaranteed to match it.
          Environment = [
            "PATH=${lib.makeBinPath (lib.optional (cfg.gpuTools != null) cfg.gpuTools)}"
          ];
        };
      };

      neuman-runtime = {
        description = "Neuman model runtimes";
        wantedBy = [ "multi-user.target" ];
        after = [ "network.target" ];
        environment = {
          NEUMAN_LLAMA_SERVER = "${llama}/bin/llama-server";
          NEUMAN_MODELS_DIR = cfg.modelsDir;
          NEUMAN_RUNTIME_CONFIG = runtimeConfig;
          NEUMAN_RUNTIME_DIR = "/run/neuman/runtime";
          NEUMAN_RUNTIME_CHAT_PORT = toString cfg.runtime.chatPort;
          NEUMAN_RUNTIME_MAX_LOADED = toString cfg.runtime.maxLoaded;
        }
        # Sampled here when a model loads, sleeps or stops, so the panel's bar
        # follows within a second without the timer running often.
        // lib.optionalAttrs cuda { NEUMAN_GPU_SAMPLE_COMMAND = sampleCommand; };
        # `nvidia-smi`, for the sampler, from the configured driver (N79).
        path = lib.optional (cuda && cfg.gpuTools != null) cfg.gpuTools;
        serviceConfig = {
          ExecStart = "${cfg.package}/bin/neuman-runtime";
          User = "neuman";
          Group = "neuman";
          Restart = "on-failure";
          RestartSec = "5";
          # Children holding VRAM are stopped first, and a 27B does not unload
          # instantly.
          TimeoutStopSec = "60";

          # The control socket and the router's file, private to the group.
          RuntimeDirectory = "neuman/runtime";
          RuntimeDirectoryMode = "0750";

          # The GPU, and only here. The server keeps `PrivateDevices`; this is
          # the unit it would otherwise have had to give up (D29, D30).
          PrivateDevices = false;
          # Loopback only. Nothing it runs needs the network: the weights are
          # already here, and fetching them is another unit's job.
          IPAddressAllow = [ "localhost" ];
          IPAddressDeny = [ "any" ];
          RestrictAddressFamilies = [
            "AF_UNIX"
            "AF_INET"
            "AF_INET6"
          ];
          # Read-only everywhere but its runtime directory and the GPU sample,
          # the weights included: a runtime has no reason to write to what it
          # loads.
          ReadWritePaths = lib.optional cuda "-/run/neuman/gpu";
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          NoNewPrivileges = true;
          MemoryDenyWriteExecute = false; # CUDA
        };
      };

      neuman-fetch = {
        description = "Neuman weights fetcher";
        wantedBy = [ "multi-user.target" ];
        after = [ "network-online.target" ];
        wants = [ "network-online.target" ];
        environment = {
          NEUMAN_MODELS_DIR = cfg.modelsDir;
          NEUMAN_FETCH_DIR = "/run/neuman/fetch";
          NEUMAN_FETCH_BASE_URL = cfg.fetch.baseUrl;
          SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
        };
        serviceConfig = {
          ExecStart = "${cfg.package}/bin/neuman-fetch";
          User = "neuman";
          Group = "neuman";
          Restart = "on-failure";
          RuntimeDirectory = "neuman/fetch";
          RuntimeDirectoryMode = "0750";
          # The weights directory and nothing else. Made by systemd when it is
          # under /var/lib, for the reason the server's own directories are: one
          # removed while starting over is recreated rather than a sandbox that
          # cannot form (N167).
          StateDirectory = lib.mkIf (lib.hasPrefix "/var/lib/" cfg.modelsDir) (
            lib.removePrefix "/var/lib/" cfg.modelsDir
          );
          StateDirectoryMode = "0770";
          ReadWritePaths = [ "-${cfg.modelsDir}" ];
          # No GPU: downloading and hashing are the network's and the disk's.
          PrivateDevices = true;
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          NoNewPrivileges = true;
          MemoryDenyWriteExecute = true;
          RestrictAddressFamilies = [
            "AF_UNIX"
            "AF_INET"
            "AF_INET6"
          ];
        };
      };

      neuman-server.environment = {
        NEUMAN_RUNTIME_CHAT_URL = "http://127.0.0.1:${toString cfg.runtime.chatPort}/v1";
        NEUMAN_RUNTIME_CONFIG = runtimeConfig;
      };
    };

    systemd.timers.neuman-gpu-sample = lib.mkIf (cfg.gpu == "cuda") {
      description = "Sample GPU state for Neuman";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "30s";
        # For changes outside Neuman — a browser or a game on the same card.
        # Neuman's own loads and unloads are sampled by the runtime host as they
        # happen, so this can be slow; one `nvidia-smi` costs ~26 ms, measured.
        OnUnitActiveSec = "15s";
        Unit = "neuman-gpu-sample.service";
      };
    };
  };
}
