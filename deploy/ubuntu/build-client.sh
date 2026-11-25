#!/usr/bin/env bash
# Build the Ubuntu 24.04 client package.
#
# Same shape as build.sh: a maintainer's machine, network for PyPI, python3
# with pip, nfpm. The client needs one distribution from PyPI (`websockets`),
# and the tools beside it are standard library only, so this is the small
# package — the browser is the person's own (D108).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-$(mktemp -d)}
STAGE=$WORK/stage
WHEELS=$WORK/wheels
OUT=${OUT:-$HERE}

VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('$ROOT/neuman-client/pyproject.toml','rb'))['project']['version'])")

# The client and the one dependency, pinned by the same file the server uses.
python3 -m pip wheel --no-deps --wheel-dir "$WHEELS" "$ROOT/neuman-client"
python3 -m pip download --dest "$WHEELS" --only-binary=:all: \
    --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_28_x86_64 \
    --python-version 3.12 --implementation cp --abi cp312 \
    -c "$HERE/constraints.txt" websockets
python3 -m pip install --no-index --find-links "$WHEELS" \
    --target "$STAGE/opt/neuman-client/lib" --no-compile --only-binary=:all: \
    --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_28_x86_64 \
    --python-version 3.12 --implementation cp --abi cp312 \
    -c "$HERE/constraints.txt" "neuman-client==$VERSION" websockets
rm -rf "$STAGE/opt/neuman-client/lib/bin"

# The registry: the client's own copy of the manifests, which is what keeps the
# server from ever naming a binary or a manifest at dispatch time (D3).
mkdir -p "$STAGE/usr/share/neuman-client/registry/packages"
for manifest in "$ROOT"/tools/*/manifest.toml; do
    name=$(basename "$(dirname "$manifest")")
    install -d "$STAGE/usr/share/neuman-client/registry/$name"
    install -m 0644 "$manifest" "$STAGE/usr/share/neuman-client/registry/$name/manifest.toml"
done
install -m 0644 "$ROOT"/tools/packages/*.toml "$STAGE/usr/share/neuman-client/registry/packages/"

# The web assets: four files, no build step.
install -d "$STAGE/usr/share/neuman-client/web"
cp -r "$ROOT/neuman-client/web/src/." "$STAGE/usr/share/neuman-client/web/"

# The tools. The Nix derivation builds one wrapper per manifest `exec` around a
# bare interpreter; this is the same arrangement with the path the package
# installs to. The Python modules are shared in lib and the wrappers name them
# by file, so a dispatch's `exec` resolves in a directory holding only the
# names manifests declare.
CLIENT_LIB=/usr/lib/neuman-client/lib
stage_lib="$STAGE$CLIENT_LIB"
stage_bin="$STAGE/usr/lib/neuman-client/bin"
install -d "$stage_lib" "$stage_bin"
cp "$ROOT"/tools/lib/*.py "$stage_lib/"

make_tool() {
    script=$1
    name=$2
    cat > "$stage_bin/$name" <<EOF
#!/usr/bin/python3
import runpy, sys
sys.path.insert(0, "$CLIENT_LIB")
runpy.run_path("$CLIENT_LIB/$script", run_name="__main__")
EOF
    chmod 0755 "$stage_bin/$name"
}

for tool in fs_read fs_write fs_edit fs_list fs_image fs_search shell web_fetch browse env_probe blender_scene blender_view; do
    cp "$ROOT/tools/bin/$tool.py" "$stage_lib/$tool.py"
    make_tool "$tool.py" "$tool"
done

# `git` is installed under its own name, and the system's git is what it execs
# — the distribution's job to keep patched.
sed "s|@git@|/usr/bin/git|" "$ROOT/tools/bin/git.py" > "$stage_lib/git.py"
make_tool git.py git

# Same two renames as the Nix package: `python` and `blender` would shadow the
# real ones on any PATH a tool's child process inherits.
cp "$ROOT/tools/bin/python.py" "$stage_lib/python.py"
make_tool python.py neuman-python
cp "$ROOT/tools/bin/blender.py" "$stage_lib/blender.py"
make_tool blender.py neuman-blender-tool
cp "$ROOT/tools/bin/blender_host.py" "$stage_lib/blender_host.py"
make_tool blender_host.py neuman-blender-host

# The wrappers, the user units and the desktop entry.
install -d "$STAGE/usr/bin" "$STAGE/usr/lib/systemd/user" "$STAGE/usr/share/applications"
install -m 0755 "$HERE"/bin/neuman-client "$HERE"/bin/neuman-browser "$STAGE/usr/bin/"
install -m 0644 "$HERE/neuman-client.service" "$HERE/neuman-browser.service" \
    "$STAGE/usr/lib/systemd/user/"
install -m 0644 "$HERE/neuman.desktop" "$STAGE/usr/share/applications/"

sed -e "s|@VERSION@|$VERSION|g" -e "s|@STAGE@|$STAGE|g" -e "s|@HERE@|$HERE|g" \
    "$HERE/nfpm-client.yaml" > "$WORK/nfpm-client.yaml"
nfpm package --config "$WORK/nfpm-client.yaml" --packager deb \
    --target "$OUT/neuman-client_${VERSION}_amd64.deb"
echo "$OUT/neuman-client_${VERSION}_amd64.deb"
