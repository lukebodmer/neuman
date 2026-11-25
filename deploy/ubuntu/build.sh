#!/usr/bin/env bash
# Build the Ubuntu 24.04 server package.
#
# This runs on a maintainer's machine, not on the appliance. It needs network
# access for PyPI, python3 with pip, and nfpm. The result is a .deb whose
# Python dependencies are exactly the versions `constraints.txt` pins — the
# set the Nix tests run against (D107).
#
# The piece to prove on a real Ubuntu 24.04 host is `scripts/postinst` and the
# unit; nothing here can test those from NixOS.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-$(mktemp -d)}
STAGE=$WORK/stage
WHEELS=$WORK/wheels
OUT=${OUT:-$HERE}

VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('$ROOT/neuman-server/pyproject.toml','rb'))['project']['version'])")

# 1. The packages this repository builds, as wheels. `pip wheel` rather than
#    `python -m build`: one tool builds them and installs them below. The
#    contracts wheel is the server's dependency and is not on PyPI, so it
#    travels with it everywhere the server is installed.
python3 -m pip wheel --no-deps --wheel-dir "$WHEELS" \
    "$ROOT/contracts" "$ROOT/neuman-server" "$ROOT/neuman-admin"

# 2. The dependency wheels, for the platform the appliance runs, pinned to the
#    versions the suite was run against. Several manylinux tags because the
#    wheels that carry C or Rust code do not all use the oldest one.
python3 -m pip download --dest "$WHEELS" --only-binary=:all: \
    --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_28_x86_64 \
    --python-version 3.12 --implementation cp --abi cp312 \
    -r "$HERE/constraints.txt"

# 3. Install them into the package's own tree. Ubuntu's interpreter runs them,
#    so nothing here needs the interpreter that built the wheels.
python3 -m pip install --no-index --find-links "$WHEELS" \
    --target "$STAGE/opt/neuman/lib" --no-compile --only-binary=:all: \
    --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_28_x86_64 \
    --python-version 3.12 --implementation cp --abi cp312 \
    -c "$HERE/constraints.txt" \
    "neuman-contracts==$VERSION" "neuman-server==$VERSION" "neuman-admin==$VERSION"

# pip puts console scripts beside the libraries under --target; the package's
# own wrappers in /usr/bin are the entry points, and a second copy of them
# inside the library tree is cruft.
rm -rf "$STAGE/opt/neuman/lib/bin"

# 4. The registry: the manifests the server gates on, as the flake's
#    neuman-registry package carries them (D3).
mkdir -p "$STAGE/usr/share/neuman/registry/packages"
for manifest in "$ROOT"/tools/*/manifest.toml; do
    name=$(basename "$(dirname "$manifest")")
    install -d "$STAGE/usr/share/neuman/registry/$name"
    install -m 0644 "$manifest" "$STAGE/usr/share/neuman/registry/$name/manifest.toml"
done
install -m 0644 "$ROOT"/tools/packages/*.toml "$STAGE/usr/share/neuman/registry/packages/"

# 5. The rest of the tree.
install -d "$STAGE/usr/bin" "$STAGE/usr/lib/systemd/system" "$STAGE/usr/lib/tmpfiles.d"
install -d "$STAGE/usr/share/neuman" "$STAGE/etc/neuman" "$STAGE/etc/apt/apt.conf.d"
install -m 0755 "$HERE"/bin/neuman-server "$HERE"/bin/neuman-admin \
    "$HERE"/bin/neuman-runtime "$HERE"/bin/neuman-fetch "$STAGE/usr/bin/"
install -m 0644 "$HERE/neuman-server.service" "$STAGE/usr/lib/systemd/system/"
install -m 0644 "$HERE/neuman.tmpfiles" "$STAGE/usr/lib/tmpfiles.d/neuman.conf"
install -m 0644 "$HERE/server.env" "$STAGE/etc/neuman/server.env"
install -m 0644 "$HERE/apt-server.conf" "$STAGE/etc/apt/apt.conf.d/51neuman-server"
printf 'deb %s\n' "$VERSION" > "$STAGE/usr/share/neuman/build-id"

# 6. Package it.
sed -e "s|@VERSION@|$VERSION|g" -e "s|@STAGE@|$STAGE|g" -e "s|@HERE@|$HERE|g" \
    "$HERE/nfpm.yaml" > "$WORK/nfpm.yaml"
nfpm package --config "$WORK/nfpm.yaml" --packager deb \
    --target "$OUT/neuman_${VERSION}_amd64.deb"
echo "$OUT/neuman_${VERSION}_amd64.deb"
