#!/usr/bin/env bash
# Build the static apt repository the packages are published from (D111).
#
# This runs on a maintainer's machine or in CI, not on an appliance, and it
# writes files only: hosting them is somebody else's job (any static host does).
#
#   deploy/ubuntu/repo.sh OUTDIR neuman_0.2.0_amd64.deb neuman-client_0.2.0_amd64.deb
#
# With NEUMAN_REPO_KEY set to a gpg key id, `InRelease` and `Release.gpg` are
# written and the key is exported beside the tree; without it the repository is
# unsigned, which apt will only accept with `[trusted=yes]` — fine for a local
# test, never for a release.
#
# The `Origin`/`Label` below are load-bearing: the client package's
# `51neuman-client` names `Neuman:stable` in `Unattended-Upgrade::Allowed-Origins`
# so that unattended upgrades may install the client and never the server. Change
# one and change the other.
#
# It needs `dpkg-dev` for `dpkg-scanpackages`, and `gpg` to sign.
set -euo pipefail

OUT=${1:?the directory to write the repository into}
shift

DIST=stable
ORIGIN=Neuman
LABEL=Neuman
POOL=$OUT/pool/main
BIN=$OUT/dists/$DIST/main/binary-amd64

mkdir -p "$POOL" "$BIN"

# One directory per source package, which is what apt's pool layout expects.
# The .debs keep their own names, so an old release stays downloadable — the
# repository is also the rollback archive (`apt install neuman=0.1.0`).
for deb in "$@"; do
    name=$(dpkg-deb -f "$deb" Package)
    version=$(dpkg-deb -f "$deb" Version)
    target=$POOL/${name:0:1}/$name
    mkdir -p "$target"
    install -m 0644 "$deb" "$target/${name}_${version}_amd64.deb"
done

(cd "$OUT" && dpkg-scanpackages --multiversion pool /dev/null > "$BIN/Packages")
gzip -9 -n -c "$BIN/Packages" > "$BIN/Packages.gz"

{
    echo "Origin: $ORIGIN"
    echo "Label: $LABEL"
    echo "Suite: $DIST"
    echo "Codename: $DIST"
    echo "Architectures: amd64"
    echo "Components: main"
    echo "Description: Neuman appliance packages"
    echo "Date: $(LC_ALL=C date -Ru)"
    echo "SHA256:"
    for file in Packages Packages.gz; do
        printf ' %s %s %s\n' \
            "$(sha256sum "$BIN/$file" | cut -d' ' -f1)" \
            "$(wc -c < "$BIN/$file")" \
            "main/binary-amd64/$file"
    done
} > "$OUT/dists/$DIST/Release"

if [ -n "${NEUMAN_REPO_KEY:-}" ]; then
    gpg --batch --yes --local-user "$NEUMAN_REPO_KEY" --digest-algo SHA256 \
        --clearsign -o "$OUT/dists/$DIST/InRelease" "$OUT/dists/$DIST/Release"
    gpg --batch --yes --local-user "$NEUMAN_REPO_KEY" --armor --detach-sign \
        -o "$OUT/dists/$DIST/Release.gpg" "$OUT/dists/$DIST/Release"
    gpg --batch --yes --armor --export "$NEUMAN_REPO_KEY" \
        > "$OUT/neuman-archive-keyring.asc"
    echo "signed with $NEUMAN_REPO_KEY"
else
    echo "warning: NEUMAN_REPO_KEY is unset; the repository is unsigned" >&2
fi

cat > "$OUT/neuman.sources" <<'SOURCES'
# Install as /etc/apt/sources.list.d/neuman.sources, with the key at
# /usr/share/keyrings/neuman-archive-keyring.gpg:
#
#   curl -fsSL https://REPLACE/neuman/neuman-archive-keyring.asc \
#     | sudo gpg --dearmor -o /usr/share/keyrings/neuman-archive-keyring.gpg
#
# The fingerprint printed by `gpg --show-keys` must match the one the release
# notes publish; fetching the key from the same host as the packages is only a
# starting point, not a check.
Types: deb
URIs: https://REPLACE/neuman
Suites: stable
Components: main
Signed-By: /usr/share/keyrings/neuman-archive-keyring.gpg
SOURCES

echo "repository written to $OUT"
echo "remember: neuman.sources has REPLACE in it"
