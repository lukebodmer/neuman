# The browser client, as the client serves it.
#
# A store path holding static files, which the client's UI server serves on
# loopback with the core's API proxied beside it, so the window, its assets and
# the API it calls share one origin.
#
# There is no build step, and that is the design rather than a shortcut. The
# client is four files with no dependencies, so "edit and reload" is the
# whole iteration loop — no bundler, no lockfile, no transpile, and nothing
# that can be out of date with respect to the source. These files are the
# untrusted tier (§3); a dependency tree here is attack surface that has to
# earn its place, and a chat window does not need one.
{ lib, stdenvNoCC }:

stdenvNoCC.mkDerivation {
  pname = "neuman-web-client";
  version = "0.1.0";
  src = ./src;

  dontBuild = true;

  installPhase = ''
    runHook preInstall
    mkdir -p $out
    cp -r ./* $out/
    runHook postInstall
  '';

  meta = {
    description = "Neuman's browser client";
    longDescription = ''
      Static files, no build step. The chat window holds the security
      handshake the server enforces — an HttpOnly session cookie the page
      cannot read, a CSRF token per unsafe request, and a WebSocket
      authenticated on upgrade rather than by a token in the URL — and renders
      one case per message type in `contracts/schemas/ws.schema.json`. The
      administration page is a second view served from the same origin.
    '';
    platforms = lib.platforms.all;
  };
}
