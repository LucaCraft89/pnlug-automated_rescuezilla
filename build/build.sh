#!/bin/bash
# PNLUG Rescuezilla — build entry point.
#
# Host-side wrapper: installs only Podman (+curl), nothing else touches this
# machine. The actual remaster (fetch latest upstream Rescuezilla ISO, apply
# PNLUG customizations, repack) runs inside a pinned Ubuntu container so the
# result is identical regardless of the host distro — see remaster.sh.
#
# Usage: build/build.sh [ubuntu-codename]
#   Codename picks which upstream Rescuezilla release variant to fetch
#   (default: noble). See the "Codename" line in a release's assets at
#   https://github.com/rescuezilla/rescuezilla/releases/latest
set -euo pipefail

CODENAME="${1:-noble}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
OUT_DIR="$SCRIPT_DIR/output"
BUILD_IMAGE="docker.io/library/ubuntu:24.04"

mkdir -p "$OUT_DIR"

ensure_podman() {
    if command -v podman >/dev/null 2>&1; then
        return
    fi
    echo "Podman not found — installing it (nothing else is touched on this machine)..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y podman curl
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y podman curl
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm podman curl
    else
        echo "Don't know how to install podman on this distro — install it manually and re-run." >&2
        exit 1
    fi
}

ensure_podman

echo "Building in a pinned Ubuntu container (target Rescuezilla codename: $CODENAME)..."
podman run --rm \
    --security-opt label=disable \
    --cap-add MKNOD \
    -v "$REPO_DIR":/pnlug/repo:ro \
    -v "$OUT_DIR":/pnlug/output:rw \
    -e "PNLUG_CODENAME=$CODENAME" \
    "$BUILD_IMAGE" \
    bash /pnlug/repo/build/remaster.sh

echo
echo "Done. Output in: $OUT_DIR"
echo "  - pnlug_zilla.iso   -> write to the Ventoy stick's root"
echo "  - ventoy/           -> copy onto the Ventoy stick (theme + auto-boot config)"
