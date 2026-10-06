#!/usr/bin/env bash
# Give this worker a Node.js of its own, so the web and screens recipes do not depend on what the machine happens to have installed.
#   ./worker/install-node.sh            # installs into tools/node in the worker folder (nothing outside it is touched, no sudo)
#   ./worker/install-node.sh --force    # reinstall
# A pinned official build (Node 22 LTS, which ships npm and corepack), checked against the SHA-256 recorded here before it is unpacked.
# The recipes (worker/recipes/lib.sh) put tools/node/bin first on PATH when it exists. Skip this on a machine whose own Node is fine:
# `python3 worker/worker.py --config worker.toml --check` says whether the recipes can run as things stand.
# Testing: NODE_DIST_BASE=file:///dir serves the tarball from a folder instead of nodejs.org.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$PWD"
VERSION="22.23.3"
BASE="${NODE_DIST_BASE:-https://nodejs.org/dist/v$VERSION}"
die() { echo "$*" >&2; exit 1; }
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) TARGET=linux-x64;       SUM=1084aa36196bba4c3a5e69a1ee388a6e4ff729dad09445fbcd434b28fe3c24af ;;
  Linux-aarch64|Linux-arm64) TARGET=linux-arm64; SUM=5ced2d48d1d7198739b7f86804de0171aefb6823b684b12341d3321afc3cb0b2 ;;
  Darwin-x86_64) TARGET=darwin-x64;     SUM=8a677b0219178efd6eb0e475457c4afb452b521a92f6e67845a73bd85727f2a8 ;;
  Darwin-arm64) TARGET=darwin-arm64;    SUM=23b25245dcfb9af7262f8ff142e9e2e0af025368117329e7a7458a51e5922f53 ;;
  *) die "no Node build is pinned for $(uname -s) $(uname -m): install Node 18 or newer yourself" ;;
esac
if [ -x tools/node/bin/node ] && [ "${1:-}" != "--force" ]; then echo "tools/node exists ($(tools/node/bin/node --version)): keeping it (--force to reinstall)"; exit 0; fi
command -v curl >/dev/null || die "curl is required"
command -v tar >/dev/null || die "tar is required"
if command -v sha256sum >/dev/null; then sha() { sha256sum "$1" | cut -d' ' -f1; }; else sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FILE="node-v$VERSION-$TARGET.tar.gz"
echo "Downloading Node v$VERSION ($TARGET)"
curl -fsSL "$BASE/$FILE" -o "$TMP/$FILE" || die "could not download $BASE/$FILE"
[ "$(sha "$TMP/$FILE")" = "$SUM" ] || die "the download does not match the checksum recorded in this script: not installing it"
mkdir -p tools "$TMP/x"
tar -xzf "$TMP/$FILE" -C "$TMP/x" || die "the Node archive is damaged"
[ -x "$TMP/x/node-v$VERSION-$TARGET/bin/node" ] || die "the Node archive has no bin/node"
rm -rf tools/node.new; mv "$TMP/x/node-v$VERSION-$TARGET" tools/node.new
rm -rf tools/node; mv tools/node.new tools/node
echo "Installed $(tools/node/bin/node --version) in $DIR/tools/node (npm $(tools/node/bin/npm --version))"
