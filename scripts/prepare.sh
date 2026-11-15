#!/usr/bin/env bash
# Fetch upstream backrest at the pinned tag and generate the hardened build context.
# Result: build/src (Docker context) with build/src/Dockerfile.hardened, and
# build/build-args (KEY=VALUE lines for docker build --build-arg).
#
# Usage: scripts/prepare.sh [upstream-ref]   (default: contents of UPSTREAM_VERSION)
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
ref="${1:-$(tr -d '[:space:]' < "$root/UPSTREAM_VERSION")}"
src="$root/build/src"
upstream="${UPSTREAM_REPO:-https://github.com/garethgeorge/backrest.git}"

rm -rf "$src"
mkdir -p "$root/build"
git clone --quiet --depth 1 --branch "$ref" "$upstream" "$src" 2>/dev/null
commit="$(git -C "$src" rev-parse HEAD)"
echo "prepare: upstream $ref @ ${commit:0:7}"

# The restic version backrest insists on (it accepts a $PATH restic >= this).
restic_version="$(sed -nE 's/^\s*RequiredResticVersion\s*=\s*"([0-9.]+)".*/\1/p' \
  "$src/internal/resticinstaller/resticinstaller.go")"
[ -n "$restic_version" ] || { echo "prepare: could not find RequiredResticVersion" >&2; exit 1; }
echo "prepare: restic $restic_version"

python3 -I "$root/scripts/harden-dockerfile.py" "$src/Dockerfile.alpine" "$src/Dockerfile.hardened"

mkdir -p "$src/hardening"
cp "$root/overlay/go-bump.sh" "$root/overlay/go-bumps.txt" "$src/hardening/"

cat > "$root/build/build-args" <<EOF
BACKREST_VERSION=$ref
BACKREST_COMMIT=$commit
BACKREST_DATE=$(git -C "$src" log -1 --format=%cI)
RESTIC_VERSION=$restic_version
EOF
echo "prepare: build context ready at $src"
