#!/bin/sh
# Builds qbsp and vis (ericw-tools 2.0, git main) with the Deep6 clip hulls
# into tools/compiler/bin. Needs:
#   sudo apt install git cmake build-essential libtbb-dev libembree-dev
# Takes a few minutes.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/ericw-tools"
REV=${ERICW_REV:-36eec1da2a194467e6baac2f444c2dd04b57d266}   # tested revision
if [ ! -d "$SRC" ]; then
    git clone --recurse-submodules https://github.com/ericwa/ericw-tools.git "$SRC"
    (cd "$SRC" && git checkout -q "$REV" && git submodule update --init --recursive)
fi
cd "$SRC"
if ! grep -q "Deep6" common/bspfile.cc; then
    # Deep6 clip hulls (map space, z up), measured from the retail BSPs:
    # hull 1: x/y +-32, z -2..+4      hull 2: x/y +-64, z -2..+4
    python3 - <<'EOF'
p = 'common/bspfile.cc'
s = open(p).read()
old = '{{0, 0, 0}, {0, 0, 0}}, {{-16, -16, -32}, {16, 16, 24}}, {{-32, -32, -64}, {32, 32, 24}}};'
new = '{{0, 0, 0}, {0, 0, 0}}, {{-32, -32, -2}, {32, 32, 4}}, {{-64, -64, -2}, {64, 64, 4}}}; /* Deep6 */'
assert s.count(old) >= 1, 'Quake hull table not found'
s = s.replace(old, new, 1)        # the first one is the Quake 1 game definition
open(p, 'w').write(s)
EOF
fi
mkdir -p build
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DSKIP_TBB_INSTALL=ON -DSKIP_EMBREE_INSTALL=ON > /dev/null
make -j"$(nproc)" qbsp vis
mkdir -p "$HERE/bin"
cp qbsp/qbsp vis/vis "$HERE/bin/"
echo "built: $HERE/bin/qbsp $HERE/bin/vis"
