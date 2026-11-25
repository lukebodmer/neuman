# The program on the user's own machine: the executor, the browser host and the
# UI.
#
# A separate package from the server because it is a separate trust position
# (SPEC section 2): it runs as the human, reaches what the human reaches, and
# holds no policy authority. The manifest set and the tool executables are build
# inputs, so the client cannot be pointed at a different set at runtime — and a
# dispatch carries a name, never a path.
#
# It installs two commands, because they are two jobs of one component: the
# daemon that executes and the browser host that backs `browse`. Chromium and
# rsync are substituted into the second's wrapper so nothing has to be on the
# user's PATH.
{
  lib,
  pkgs,
  python3,
  makeWrapper,
  chromium,
  rsync,
  neuman-registry,
  neuman-tools,
  # The chat window, in `web/` beside this file. A build input rather than
  # something fetched, so the UI and the executor it talks to cannot be versions
  # apart; the default is the source tree, and there is rarely a reason to
  # override it.
  neuman-web-client ? pkgs.callPackage ./web { },
}:

python3.pkgs.buildPythonApplication {
  pname = "neuman-client";
  version = "0.1.0";
  src = ./.;
  format = "pyproject";

  # `websockets` is the transport: the daemon holds one authenticated socket to
  # the core, and it is pure Python, so the client stays portable.
  propagatedBuildInputs = [ python3.pkgs.websockets ];

  nativeBuildInputs = [
    python3.pkgs.setuptools
    makeWrapper
  ];

  # No test here: the executor's tests need a real process tree, the tool host's
  # need real manifests, and both are what `client-test` runs. This package only
  # has to build.
  doCheck = false;

  postFixup = ''
    wrapProgram $out/bin/neuman-client \
      --set NEUMAN_CLIENT_MANIFESTS ${neuman-registry} \
      --set NEUMAN_CLIENT_BIN ${neuman-tools}/bin \
      --set NEUMAN_CLIENT_WEB ${neuman-web-client} \
      --set NEUMAN_CLIENT_BUILD_ID ${placeholder "out"}
    wrapProgram $out/bin/neuman-browser \
      --set NEUMAN_BROWSER_CHROMIUM ${lib.getExe chromium} \
      --set NEUMAN_BROWSER_RSYNC ${rsync}/bin/rsync
  '';

  meta = {
    description = "Run Neuman's tools on the user's own machine";
    mainProgram = "neuman-client";
    platforms = lib.platforms.linux;
  };
}
