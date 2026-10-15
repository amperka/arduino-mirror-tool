#!/usr/bin/env bash
#
# Build XOD __packages__ archives for three platforms.
#
# All required files are downloaded from the Arduino mirror (MIRROR_HOST) and
# esp8266.com. Extract an archive into the user's workspace directory:
#   ~/xod/__packages__/
#
# Usage (CI-friendly):
#   MIRROR_HOST=https://arduino-downloads.amperka.ru OUT=./dist \
#     scripts/build-xod-fix-packages.sh
#
set -euo pipefail

export LC_ALL=C

MIRROR_HOST="${MIRROR_HOST:-https://arduino-downloads.amperka.ru}"
OUT="${OUT:-dist}"
ESP8266_INDEX_URL="${ESP8266_INDEX_URL:-https://arduino.esp8266.com/stable/package_esp8266com_index.json}"

MTOOLS="$MIRROR_HOST/p/tools"
PACKAGES_INDEX_URL="$MIRROR_HOST/p/packages/package_index.json"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$OUT"

echo "==> Mirror: $MIRROR_HOST"
echo "==> Output: $OUT"

# ---------------------------------------------------------------------------
# Shared files for all platforms
# ---------------------------------------------------------------------------
echo "==> Downloading package_index.json..."
curl -fsSL -o "$TMP/package_index.json" "$PACKAGES_INDEX_URL"

echo "==> Downloading package_esp8266com_index.json..."
curl -fsSL -o "$TMP/package_esp8266com_index.json" "$ESP8266_INDEX_URL"

# XOD does not use the library index. arduino-cli requires a valid placeholder
# file so that it does not download the blocked downloads.arduino.cc index.
printf '{"libraries": []}' >"$TMP/library_index.json"

# extra.txt contains the mirrored package index and ESP8266 index URLs.
printf '%s\n%s\n' "$PACKAGES_INDEX_URL" "$ESP8266_INDEX_URL" >"$TMP/extra.txt"

# ---------------------------------------------------------------------------
# Build one platform archive.
#   $1 = platform: linux | windows | macos
# ---------------------------------------------------------------------------
build_platform() {
  local plat="$1"
  local base="$TMP/$plat"
  local tools="$base/__packages__/packages/builtin/tools"
  local ctags_dir="$tools/ctags/5.8-arduino11"
  local sd_dir="$tools/serial-discovery/1.0.0"

  echo "==> Building: $plat"

  mkdir -p "$base/__packages__" "$ctags_dir" "$sd_dir"
  cp "$TMP/package_index.json" "$base/__packages__/package_index.json"
  cp "$TMP/package_esp8266com_index.json" "$base/__packages__/package_esp8266com_index.json"
  cp "$TMP/library_index.json" "$base/__packages__/library_index.json"
  cp "$TMP/extra.txt" "$base/__packages__/extra.txt"

  case "$plat" in
  linux)
    # ctags tar.bz2 archive containing ctags-5.8-arduino11/ctags.
    curl -fsSL -o "$TMP/ctags.tar.bz2" \
      "$MTOOLS/ctags-5.8-arduino11-pm-x86_64-pc-linux-gnu.tar.bz2"
    tar xjf "$TMP/ctags.tar.bz2" --strip-components=1 -C "$ctags_dir"

    # serial-discovery tar.bz2 archive containing bin/serial-discovery.
    # arduino-cli 0.12.0 expects the executable at the version directory root.
    curl -fsSL -o "$TMP/sd.tar.bz2" \
      "$MTOOLS/serial-discovery-linux64-v1.0.0.tar.bz2"
    mkdir -p "$TMP/sd-linux"
    tar xjf "$TMP/sd.tar.bz2" --strip-components=1 -C "$TMP/sd-linux"
    mv "$TMP/sd-linux/serial-discovery" "$sd_dir/serial-discovery"

    chmod +x "$ctags_dir/ctags" "$sd_dir/serial-discovery"
    tar czf "$OUT/xod-packages-linux-x86_64.tar.gz" -C "$base" __packages__
    ;;

  windows)
    # ctags ZIP archive containing ctags-5.8-arduino11/ctags.exe.
    curl -fsSL -o "$TMP/ctags.zip" \
      "$MTOOLS/ctags-5.8-arduino11-pm-i686-mingw32.zip"
    python3 -c "import zipfile; zipfile.ZipFile('$TMP/ctags.zip').extractall('$TMP/ctags-win')"
    cp "$TMP/ctags-win/ctags-5.8-arduino11/ctags.exe" "$ctags_dir/ctags.exe"

    # serial-discovery ZIP archive containing bin/serial-discovery.exe.
    curl -fsSL -o "$TMP/sd.zip" \
      "$MTOOLS/serial-discovery-windows-v1.0.0.zip"
    python3 -c "import zipfile; zipfile.ZipFile('$TMP/sd.zip').extractall('$TMP/sd-win')"
    cp "$TMP/sd-win/bin/serial-discovery.exe" "$sd_dir/serial-discovery.exe"

    python3 - "$OUT" "$base" <<'PYEOF'
import os, sys, zipfile
out, base = sys.argv[1], sys.argv[2]
path = os.path.join(out, 'xod-packages-windows-x86_64.zip')
with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(os.path.join(base, '__packages__')):
        for f in files:
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, base))
PYEOF
    ;;

  macos)
    # ctags ZIP archive containing ctags-5.8-arduino11/ctags.
    curl -fsSL -o "$TMP/ctags.zip" \
      "$MTOOLS/ctags-5.8-arduino11-pm-x86_64-apple-darwin.zip"
    python3 -c "import zipfile; zipfile.ZipFile('$TMP/ctags.zip').extractall('$TMP/ctags-mac')"
    cp "$TMP/ctags-mac/ctags-5.8-arduino11/ctags" "$ctags_dir/ctags"

    # serial-discovery tar.bz2 archive containing bin/serial-discovery.
    curl -fsSL -o "$TMP/sd.tar.bz2" \
      "$MTOOLS/serial-discovery-macosx-v1.0.0.tar.bz2"
    mkdir -p "$TMP/sd-mac"
    tar xjf "$TMP/sd.tar.bz2" --strip-components=1 -C "$TMP/sd-mac"
    mv "$TMP/sd-mac/serial-discovery" "$sd_dir/serial-discovery"

    chmod +x "$ctags_dir/ctags" "$sd_dir/serial-discovery"
    tar czf "$OUT/xod-packages-macos-x86_64.tar.gz" -C "$base" __packages__
    ;;

  *)
    echo "Unknown platform: $plat" >&2
    exit 1
    ;;
  esac
}

build_platform linux
build_platform windows
build_platform macos

echo ""
echo "==> Complete:"
ls -la "$OUT"
