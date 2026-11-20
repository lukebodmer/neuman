# The tool binaries, as the appliance installs them.
#
# Separate from `neuman-registry`, which is manifests only, because they are
# two different trust positions (D3): a manifest declares what a tool needs and
# is read by the server *and* by every client; the binary a name resolves to is
# the client's own trusted configuration and is never supplied by a dispatch.
# Keeping them apart is what makes "the server cannot name a binary" a
# structural fact rather than a convention.
#
# `bin/` holds one entry point per tool; `lib/` holds the module that does the
# work, which is where a tool needs one at all — `browse` imports its CDP
# client, the Blender tools their socket client, `python` its environment
# resolver, `fs_write`/`fs_edit` the planner they share. Split in the source tree because that is how they are installed:
# a wrapper in `$out/bin` runs the module in `$out/lib` with `$out/lib` on
# `sys.path`, so the tool directory a dispatch's `exec` resolves in holds
# nothing but the names manifests declare.
{
  lib,
  stdenvNoCC,
  python3,
  # The real git the `git` tool wrapper execs. A build input rather than a PATH
  # lookup: the tools run on the user's machine, where their `git` may be any
  # version, and the tool is the one this appliance was built and tested with.
  git,
}:

let
  # A bare interpreter, deliberately: every import a tool makes beyond the
  # standard library is one this derivation has to carry, and the tools are
  # small enough to stay on the standard library (N47).
  python = python3.withPackages (_: [ ]);
in
stdenvNoCC.mkDerivation {
  pname = "neuman-tools";
  version = "0.1.0";
  src = ./.;

  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/lib

    cp lib/*.py $out/lib/

    install_tool() {
      local script="$1" name="$2"
      cat > "$out/bin/$name" <<EOF
    #!${python}/bin/python3
    import runpy, sys
    sys.path.insert(0, "$out/lib")
    runpy.run_path("$out/lib/$script", run_name="__main__")
    EOF
      sed -i 's/^    //' "$out/bin/$name"
      chmod +x "$out/bin/$name"
    }

    # The tools a manifest names with `exec`.
    for tool in fs_read fs_write fs_edit fs_list fs_image fs_search shell web_fetch browse env_probe blender_scene blender_view; do
      cp "bin/$tool.py" "$out/lib/$tool.py"
      install_tool "$tool.py" "$tool"
    done

    # `git`, installed as `git`. A wrapper rather than the binary itself: it
    # turns the dispatch's JSON argument into a git argv and sets the
    # environment a headless git needs.
    substitute bin/git.py "$out/lib/git.py" --replace-fail '@git@' '${git}/bin/git'
    install_tool git.py git

    # The `python` tool, installed as `neuman-python` rather than as `python`,
    # so a script the tool runs that shells out to `python` gets the
    # interpreter its environment provides rather than re-entering the tool
    # with a JSON argument it would reject. `exec` is resolved by joining the
    # bin directory with the name, so only that resolution needs this file.
    cp bin/python.py $out/lib/python.py
    install_tool python.py neuman-python

    # `blender`, installed as `neuman-blender-tool` for the same reason: a file
    # called `blender` would shadow Blender on any PATH this directory reaches.
    cp bin/blender.py $out/lib/blender.py
    install_tool blender.py neuman-blender-tool

    # The Blender package's host (D101). Named by the package file, never by a
    # manifest, so the client can start it and no dispatch can.
    cp bin/blender_host.py $out/lib/blender_host.py
    install_tool blender_host.py neuman-blender-host

    runHook postInstall
  '';

  meta = {
    description = "Neuman's tool binaries";
    longDescription = ''
      The executables a manifest's `exec` resolves to. A tool does no checking
      of its own beyond what its manifest declares: it runs as the acting user
      on their machine, reaching exactly what they reach. The declared-surface
      check lives in the client's tool host, which is the side that knows which
      paths a dispatch was granted.
    '';
    platforms = lib.platforms.linux;
  };
}
